"""Operaciones humanas de la fabrica como funciones importables (CLI y Factory Studio).

Cada funcion recibe un `Project`, valida igual que antes la CLI y lanza `FactoryError` con
un mensaje para el humano. Las garantias no cambian: las decisiones solo se registran con
el run detenido en ese gate, firmadas por un rol autorizado, y nunca hay merge.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .project import Project, normalize_change_id
from .roles import FactoryError, validate_role

VALID_GATES = ("requirements", "plan", "review", "release")
VALID_DECISIONS = ("APPROVED", "MODIFIED", "REJECTED", "DEFERRED")
VERDICTS = ("ACCEPTED", "REJECTED", "MODIFIED", "DEFERRED")
FINDING_RE = re.compile(r"^(RF|SEC|SEC-SCAN)-\d{2,}$")
REPORT_ID_RE = re.compile(r"\b(?:RF|SEC(?:-SCAN)?)-\d{2,}\b")
TERMINAL = ("READY FOR HUMAN MERGE", "NOT READY", "ESCALATED")
EXPORT_REQUIRED = (
    "00-change-request.md",
    "factory-run.json",
    "traceability.md",
    "14-evidence-summary.md",
)

__all__ = [
    "FactoryError",
    "approve_gate",
    "create_change_request",
    "decide_findings",
    "export_evidence",
    "find_change_request",
    "list_change_requests",
    "list_runs",
    "read_state",
    "report_finding_ids",
    "retry_run",
    "start_run",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cid(change_id: str) -> str:
    try:
        return normalize_change_id(change_id)
    except ValueError as exc:
        raise FactoryError(str(exc)) from exc


def _state_path(project: Project, change_id: str) -> Path:
    return project.run_dir(change_id) / "factory-state.json"


def read_state(project: Project, change_id: str) -> dict:
    path = _state_path(project, _cid(change_id))
    if not path.is_file():
        raise FactoryError(f"Run not found: {change_id}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- change requests
def list_change_requests(project: Project) -> list[dict]:
    out = []
    if not project.cr_dir.is_dir():
        return out
    for path in sorted(project.cr_dir.glob("CR-*.md")):
        m = re.match(r"^(CR-[A-Za-z0-9]+)", path.stem)
        if not m:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        title = next(
            (line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")),
            path.stem,
        )
        cid = m.group(1).upper()
        out.append(
            {
                "change_id": cid,
                "file": path.relative_to(project.root).as_posix(),
                "title": title,
                "has_run": _state_path(project, cid).is_file(),
            }
        )
    return out


def find_change_request(project: Project, change_id: str) -> Path:
    cid = _cid(change_id)
    matches = sorted(project.cr_dir.glob(f"{cid}-*.md")) + sorted(project.cr_dir.glob(f"{cid}.md"))
    if len(matches) != 1:
        raise FactoryError(f"Expected one Change Request for {cid}; found {len(matches)}")
    return matches[0]


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "change"


def create_change_request(project: Project, change_id: str, title: str, body: str) -> Path:
    """Crea `<change_requests>/<CR>-<slug>.md`. No sobrescribe CRs existentes."""
    cid = _cid(change_id)
    if not title.strip():
        raise FactoryError("El Change Request necesita un titulo")
    if list(project.cr_dir.glob(f"{cid}-*.md")) or (project.cr_dir / f"{cid}.md").exists():
        raise FactoryError(f"Ya existe un Change Request {cid}")
    project.cr_dir.mkdir(parents=True, exist_ok=True)
    path = project.cr_dir / f"{cid}-{_slug(title)}.md"
    content = body if body.lstrip().startswith("#") else f"# {cid} — {title.strip()}\n\n{body}"
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def update_change_request(project: Project, change_id: str, body: str) -> Path:
    """Edita un CR SIN run: con run iniciado el CR es evidencia (00-change-request.md)."""
    path = find_change_request(project, change_id)
    if _state_path(project, _cid(change_id)).is_file():
        raise FactoryError(f"{change_id} ya tiene un run: el CR quedo congelado como evidencia")
    path.write_text(body.rstrip() + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- runs
def initial_state(change_id: str) -> dict:
    return {
        "change_id": change_id,
        "created_at": _now(),
        "status": "INTAKE_PENDING",
        "human_gates": {g: "PENDING" for g in VALID_GATES},
        # mismo shape que escribe el motor (factory-run.json nunca mezcla str y dict)
        "verification": {"status": "NOT_RUN", "tests": 0, "failed": 0, "lint": "NOT_RUN"},
        "review": {"status": "NOT_RUN", "must_fix": 0, "should_fix": 0},
        "security": {"status": "NOT_RUN", "high": 0, "medium": 0},
        "iterations": 0,
    }


def start_run(project: Project, change_id: str) -> dict:
    cid = _cid(change_id)
    source = find_change_request(project, cid)
    run = project.run_dir(cid)
    if run.exists():
        raise FactoryError(f"Run already exists: {run.relative_to(project.root).as_posix()}")
    run.mkdir(parents=True)
    shutil.copy2(source, run / "00-change-request.md")
    state = initial_state(cid)
    (run / "factory-state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


RETRYABLE = ("ESCALATED", "NOT READY")
ATTEMPTS_DIR = "intentos"


def retry_run(
    project: Project,
    change_id: str,
    body: str,
    reason: str,
    recorded_by: str,
    force: bool = False,
) -> dict:
    """Reintentar: nuevo intento del MISMO CR (editado) tras ESCALATED / NOT READY.

    No reanuda nada: el intento anterior se archiva entero en `runs/<CR>/intentos/<n>/`,
    su rama se renombra a `<rama>-intento<n>` (se conserva) y el nuevo intento empieza en
    INTAKE_PENDING con todos los gates en PENDING. Firma el Product Owner (roles del gate
    requirements). Superado `limits.attempts` exige `force` (decision explicita)."""
    from .context import use_project
    from .orchestrator import archive_run_branch

    cid, reason = _cid(change_id), reason.strip()
    state = read_state(project, cid)
    status = state.get("status")
    if status not in RETRYABLE:
        raise FactoryError(
            f"Solo se reintenta un run ESCALATED o NOT READY ({cid} esta en {status})"
        )
    if not reason:
        raise FactoryError("Reintentar requiere un motivo")
    if not body.strip():
        raise FactoryError("El Change Request no puede quedar vacio")
    if not recorded_by.strip() or "TODO" in recorded_by:
        raise FactoryError('Identifique al humano: "Nombre (Rol)"')
    role = validate_role("requirements", recorded_by, project.config.gates.roles)
    attempt = int(state.get("attempt", 1))
    cap = project.config.limits.attempts
    if attempt >= cap and not force:
        raise FactoryError(
            f"{cid} ya lleva {attempt} intento(s) (tope {cap}). Si el CR sigue sin salir, "
            "considera dividirlo o reescribir su alcance; reintenta de todos modos solo si "
            "lo decides explicitamente.",
            code="attempts_exhausted",
        )

    run = project.run_dir(cid)
    source = find_change_request(project, cid)
    previous_cr = (run / "00-change-request.md").read_text(encoding="utf-8", errors="replace")
    archive = run / ATTEMPTS_DIR / str(attempt)
    if archive.exists():
        raise FactoryError(f"Ya existe el archivo del intento {attempt}: {archive}")
    with use_project(project):
        branch = archive_run_branch(cid, attempt)
    archive.mkdir(parents=True)
    for item in sorted(run.iterdir()):
        if item.name != ATTEMPTS_DIR:
            shutil.move(str(item), str(archive / item.name))

    text = body.rstrip() + "\n"
    source.write_text(text, encoding="utf-8")
    (run / "00-change-request.md").write_text(text, encoding="utf-8")
    now = _now()
    previous = [
        *state.get("previous_attempts", []),
        {
            "n": attempt,
            "status": status,
            "stop_reason": state.get("stop_reason", ""),
            "branch": branch,
            "archive": archive.relative_to(project.root).as_posix(),
            "archived_at": now,
        },
    ]
    retry = {
        "by": recorded_by,
        "role": role,
        "reason": reason,
        "at": now,
        "forced": bool(attempt >= cap),
        "cr_changed": previous_cr.rstrip() != body.rstrip(),
    }
    new = {
        **initial_state(cid),
        "attempt": attempt + 1,
        "previous_attempts": previous,
        "retry": retry,
        "transitions": [{"status": "INTAKE_PENDING", "event": "RETRY", "at": now}],
    }
    (run / "factory-state.json").write_text(json.dumps(new, indent=2), encoding="utf-8")
    (run / "00-retry-decision.md").write_text(
        "\n".join(
            [
                f"# Reintento — {cid} (intento {attempt + 1})",
                "",
                f"- Decidido por: {recorded_by}",
                f"- Fecha: {now}",
                f"- Motivo: {reason}",
                f"- Intento anterior: #{attempt} {status} — {state.get('stop_reason') or '-'}",
                f"- Archivo del intento anterior: {previous[-1]['archive']}",
                f"- Rama conservada: {branch or '(no llego a crear rama)'}",
                f"- CR modificado: {'si' if retry['cr_changed'] else 'no'}",
                *(
                    [f"- Tope de intentos ({cap}) superado por decision explicita"]
                    if retry["forced"]
                    else []
                ),
                "",
                "> Nuevo intento: todos los gates humanos vuelven a PENDING. Nada se reanuda.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return new


def list_runs(project: Project) -> list[dict]:
    out = []
    if not project.runs_dir.is_dir():
        return out
    for state_path in sorted(project.runs_dir.glob("CR-*/factory-state.json")):
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "change_id": st.get("change_id", state_path.parent.name),
                "status": st.get("status"),
                "awaiting_gate": st.get("awaiting_gate") or "",
                "stop_reason": st.get("stop_reason") or "",
                "created_at": st.get("created_at"),
                "attempt": st.get("attempt", 1),
                "paused": bool(st.get("paused")),
            }
        )
    return out


def report_finding_ids(run: Path) -> set[str]:
    """IDs de finding presentes en los reportes de la ronda actual."""
    text = ""
    for name in ("08-review-report.md", "09-security-report.md"):
        if (run / name).is_file():
            text += (run / name).read_text(encoding="utf-8") + "\n"
    return set(REPORT_ID_RE.findall(text))


def _decided_ids(run: Path) -> set[str]:
    path = run / "human-findings-decisions.json"
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {d["finding_id"] for d in data.get("decisions", [])}


def approve_gate(
    project: Project, change_id: str, gate: str, decision: str, reason: str, recorded_by: str
) -> dict:
    cid, gate, decision, reason = _cid(change_id), gate.lower(), decision.upper(), reason.strip()
    if gate not in VALID_GATES:
        raise FactoryError(f"Invalid gate: {sorted(VALID_GATES)}")
    if decision not in VALID_DECISIONS:
        raise FactoryError(f"Invalid decision: {sorted(VALID_DECISIONS)}")
    if not reason:
        raise FactoryError("Human decision requires a reason")
    if not recorded_by.strip() or "TODO" in recorded_by:
        raise FactoryError('Identifique al humano: "Nombre (Rol)"')
    role = validate_role(gate, recorded_by, project.config.gates.roles)
    state = read_state(project, cid)
    run = project.run_dir(cid)
    awaiting = state.get("awaiting_gate") or ""
    if awaiting != gate:
        raise FactoryError(
            f"La fabrica no esta esperando el gate '{gate}' en {cid} "
            f"(status={state.get('status')}, esperando: {awaiting or 'ningun gate'}). "
            "Las decisiones solo se registran cuando el run se detiene en su gate: "
            "avance el run primero."
        )
    if gate == "review" and decision in {"APPROVED", "MODIFIED"}:
        missing = sorted(report_finding_ids(run) - _decided_ids(run))
        if missing:
            raise FactoryError(
                f"Findings sin clasificar en {cid}: {', '.join(missing)}. "
                "Clasificalos (ACCEPTED|REJECTED|MODIFIED|DEFERRED) como Human Reviewer "
                "antes de cerrar el gate review."
            )
    # human_gates lo actualiza el motor al CONSUMIR la decision, no esta funcion.
    evidence = {
        "change_id": cid,
        "gate": gate,
        "decision": decision,
        "reason": reason,
        "recorded_at": _now(),
        "recorded_by": recorded_by,
        "role": role,
        "note": "Decisión humana explícita; la fábrica no interpreta silencio.",
    }
    path = run / f"human-{gate}-decision.json"
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return evidence


def decide_findings(
    project: Project, change_id: str, verdicts: dict[str, str], recorded_by: str
) -> dict:
    """Clasificacion humana por finding. Las llamadas de una ronda se ACUMULAN (la ultima
    decision por finding gana). Devuelve {"decisions", "pending", "path"}."""
    cid = _cid(change_id)
    role = validate_role("findings", recorded_by, project.config.gates.roles)
    if not verdicts:
        raise FactoryError("Include at least one FINDING=VERDICT pair")
    decisions = []
    for fid, verdict in verdicts.items():
        fid, verdict = fid.strip().upper(), verdict.strip().upper()
        if not FINDING_RE.match(fid):
            raise FactoryError(f"finding id invalido: {fid} (usa RF-##, SEC-## o SEC-SCAN-##)")
        if verdict not in VERDICTS:
            raise FactoryError(f"verdict invalido para {fid}: {verdict} -> {sorted(VERDICTS)}")
        decisions.append({"finding_id": fid, "decision": verdict})
    state = read_state(project, cid)
    if state.get("awaiting_gate") != "review":
        raise FactoryError(
            f"La fabrica no esta esperando el gate review en {cid} "
            f"(status={state.get('status')}); los findings se clasifican cuando el run se "
            "detiene en review."
        )
    run = project.run_dir(cid)
    known = report_finding_ids(run)
    unknown = [d["finding_id"] for d in decisions if known and d["finding_id"] not in known]
    if unknown:
        raise FactoryError(f"findings no presentes en los reportes del run: {unknown}")
    path = run / "human-findings-decisions.json"
    merged: dict[str, dict] = {}
    if path.is_file():  # misma ronda: se acumula (las rondas previas ya fueron archivadas)
        for d in json.loads(path.read_text(encoding="utf-8")).get("decisions", []):
            merged[d["finding_id"]] = d
    for d in decisions:
        merged[d["finding_id"]] = d
    payload = {
        "change_id": cid,
        "gate": "findings",
        "decisions": sorted(merged.values(), key=lambda d: d["finding_id"]),
        "recorded_by": recorded_by,
        "role": role,
        "recorded_at": _now(),
        "note": "Los ACCEPTED/MODIFIED bloqueantes autorizan Coder Fix; el resto no cambia codigo.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "decisions": payload["decisions"],
        "pending": sorted(known - set(merged)),
        "path": path.relative_to(project.root).as_posix(),
    }


def export_evidence(project: Project, change_id: str) -> dict:
    cid = _cid(change_id)
    state = read_state(project, cid)
    run = project.run_dir(cid)
    missing = [a for a in EXPORT_REQUIRED if not (run / a).is_file()]
    if missing:
        raise FactoryError(
            f"Evidencia incompleta para exportar: faltan {missing} (estado: {state['status']})"
        )
    if state["status"] not in TERMINAL:
        raise FactoryError(
            f"El run aun no termino (status={state['status']}). "
            "Exporta solo tras el release gate o un escalamiento registrado."
        )
    dest = project.evidence_dir / cid
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    exported = []
    for f in sorted(run.iterdir()):
        if f.is_file():
            shutil.copy2(f, dest / f.name)
            exported.append(f.name)
    return {"files": exported, "dest": dest.relative_to(project.root).as_posix()}
