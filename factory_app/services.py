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
FINDING_RE = re.compile(r"^(RF|HR|SEC|SEC-SCAN)-\d{2,}$")
REPORT_ID_RE = re.compile(r"\b(?:RF|HR|SEC(?:-SCAN)?)-\d{2,}\b")
# mismos nombres que orchestrator.FINDING_REPORTS (services no importa el motor)
HUMAN_FINDINGS = "08b-human-review-findings.md"
FINDING_REPORTS = ("08-review-report.md", HUMAN_FINDINGS, "09-security-report.md")
FIX_VERDICTS = ("ACCEPTED", "MODIFIED")  # los unicos que pueden autorizar un Coder Fix
HUMAN_SEVERITIES = ("MUST FIX", "SHOULD FIX")  # findings que añade el Human Reviewer
MAX_FINDING_TEXT = 2000
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
    "archive_change_request",
    "cr_lifecycle",
    "delete_change_request",
    "create_change_request",
    "decide_findings",
    "export_evidence",
    "find_change_request",
    "list_change_requests",
    "list_runs",
    "read_state",
    "report_finding_ids",
    "restore_change_request",
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
        lines = text.splitlines()
        cid = m.group(1).upper()
        title = next((line[2:].strip() for line in lines if line.startswith("# ")), None)
        if title is None:
            # sin H1 (CRs antiguos): el nombre del archivo dice mas que su primer "## Problema"
            slug = path.stem[len(m.group(1)) :].strip("-_ ")
            title = slug.replace("-", " ").replace("_", " ").strip().capitalize() or next(
                (line.lstrip("# ").strip() for line in lines if line.startswith("#")), cid
            )
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
    # solo un H1 propio cuenta como titulo: "## Problema" no lo es
    content = body if body.lstrip().startswith("# ") else f"# {cid} — {title.strip()}\n\n{body}"
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def update_change_request(
    project: Project, change_id: str, body: str, busy: bool = False
) -> Path:
    """Edita un CR mientras su intake no corrio (sin run, o run creado sin avanzar).

    Despues el CR es evidencia (00-change-request.md) y queda congelado; para cambiarlo
    tras un ESCALATED / NOT READY esta Reintentar."""
    cid = _cid(change_id)
    path = find_change_request(project, cid)
    life = cr_lifecycle(project, cid, busy=busy)
    if not life["editable"]:
        raise FactoryError(f"{cid}: {life['reason']}", code="cr_frozen")
    if not body.strip():
        raise FactoryError("El Change Request no puede quedar vacio")
    text = body.rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    copy = project.run_dir(cid) / "00-change-request.md"
    if copy.is_file():  # run creado sin avanzar: su copia de evidencia sigue al CR
        copy.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- ciclo de vida del CR
ARCHIVE_DIR = "archivados"
ARCHIVE_RECORD = "archivo.json"


def _finished(state: dict) -> bool:
    """Terminado de verdad: listo, no listo, o escalado sin gate que lo reanude."""
    status = state.get("status")
    if status == "ESCALATED":
        return not state.get("awaiting_gate")
    return status in ("READY FOR HUMAN MERGE", "NOT READY")


def cr_lifecycle(project: Project, change_id: str, busy: bool = False) -> dict:
    """Que admite el CR ahora: editarse, archivarse, eliminarse (y por que no)."""
    cid = _cid(change_id)
    run = project.run_dir(cid)
    state_path = run / "factory-state.json"
    if not state_path.is_file():
        return {"editable": True, "archivable": False, "deletable": True, "reason": ""}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    untouched = (
        state.get("status") == "INTAKE_PENDING"
        and not state.get("paused")
        and not (run / "01-intake-report.md").exists()
    )
    finished = _finished(state)
    if busy:
        reason = "el run esta avanzando; espera a que se detenga"
    elif untouched:
        reason = ""
    elif finished:
        reason = "el run termino: el CR es evidencia; usa Reintentar para cambiarlo"
    else:
        reason = "el intake ya corrio: el CR quedo congelado como evidencia del run"
    return {
        "editable": untouched and not busy,
        "archivable": finished and not busy,
        "deletable": finished and not busy,
        "reason": reason,
    }


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise FactoryError(f"Ruta fuera de la carpeta de la fabrica: {path}")
    return resolved


def _archived_cr(project: Project, cid: str) -> Path | None:
    folder = project.cr_dir / ARCHIVE_DIR
    matches = sorted(folder.glob(f"{cid}-*.md")) + sorted(folder.glob(f"{cid}.md"))
    return matches[0] if len(matches) == 1 else None


def list_archived(project: Project) -> list[dict]:
    """CRs archivados con su registro (quien, cuando, estado final)."""
    out = []
    folder = project.cr_dir / ARCHIVE_DIR
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("CR-*.md")):
        m = re.match(r"^(CR-[A-Za-z0-9]+)", path.stem)
        if not m:
            continue
        cid = m.group(1).upper()
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        title = next((line[2:].strip() for line in lines if line.startswith("# ")), path.stem)
        record_path = project.runs_dir / ARCHIVE_DIR / cid / ARCHIVE_RECORD
        record = {}
        if record_path.is_file():
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                record = {}
        out.append({
            "change_id": cid,
            "file": path.relative_to(project.root).as_posix(),
            "title": title,
            "archived": True,
            "archived_at": record.get("archived_at"),
            "archived_by": record.get("archived_by"),
            "status": record.get("status"),
        })
    return out


def _drop_worktree(project: Project, cid: str) -> bool:
    from .context import use_project
    from .orchestrator import remove_worktree

    with use_project(project):
        return remove_worktree(cid)


def archive_change_request(
    project: Project, change_id: str, recorded_by: str, busy: bool = False
) -> dict:
    """Archiva un CR terminado y su run (se ocultan, se pueden restaurar). La rama queda."""
    cid = _cid(change_id)
    life = cr_lifecycle(project, cid, busy=busy)
    if busy:
        raise FactoryError(life["reason"], code="busy")
    if not life["archivable"]:
        raise FactoryError(
            f"{cid}: solo se archiva un run terminado (listo, no listo o escalado sin "
            "reanudacion)", code="not_finished")
    source = _inside(find_change_request(project, cid), project.cr_dir)
    run = _inside(project.run_dir(cid), project.runs_dir)
    cr_dest = project.cr_dir / ARCHIVE_DIR / source.name
    run_dest = project.runs_dir / ARCHIVE_DIR / cid
    if cr_dest.exists() or run_dest.exists():
        raise FactoryError(f"Ya hay un {cid} archivado: restauralo o eliminalo antes",
                           code="exists")
    state = json.loads((run / "factory-state.json").read_text(encoding="utf-8"))
    worktree_removed = _drop_worktree(project, cid)
    cr_dest.parent.mkdir(parents=True, exist_ok=True)
    run_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(cr_dest))
    shutil.move(str(run), str(run_dest))
    record = {
        "change_id": cid,
        "archived_at": _now(),
        "archived_by": recorded_by,
        "status": state.get("status"),
        "branch": state.get("branch"),
        "worktree_removed": worktree_removed,
    }
    (run_dest / ARCHIVE_RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def restore_change_request(project: Project, change_id: str) -> dict:
    """Devuelve un CR archivado (y su run) a la lista activa."""
    cid = _cid(change_id)
    source = _archived_cr(project, cid)
    if source is None:
        raise FactoryError(f"No hay un {cid} archivado")
    run_src = project.runs_dir / ARCHIVE_DIR / cid
    if list(project.cr_dir.glob(f"{cid}-*.md")) or (project.cr_dir / f"{cid}.md").exists() \
            or project.run_dir(cid).exists():
        raise FactoryError(f"Ya existe un {cid} activo: no se puede restaurar encima",
                           code="exists")
    shutil.move(str(source), str(project.cr_dir / source.name))
    if run_src.is_dir():
        (run_src / ARCHIVE_RECORD).unlink(missing_ok=True)
        shutil.move(str(run_src), str(project.run_dir(cid)))
    return {"change_id": cid, "restored": True}


def delete_change_request(
    project: Project, change_id: str, confirm: str, busy: bool = False
) -> dict:
    """Elimina para siempre un CR (sin run, o con run terminado) y su evidencia.

    Exige teclear el ID. Borra el worktree pero conserva la rama de git. Vale tambien para
    un CR archivado."""
    cid = _cid(change_id)
    if (confirm or "").strip().upper() != cid:
        raise FactoryError(f"Escribe {cid} para confirmar la eliminacion",
                           code="confirm_mismatch")
    archived = _archived_cr(project, cid)
    removed: list[str] = []
    if archived is not None and not list(project.cr_dir.glob(f"{cid}-*.md")):
        run = project.runs_dir / ARCHIVE_DIR / cid
        source = archived
    else:
        life = cr_lifecycle(project, cid, busy=busy)
        if busy:
            raise FactoryError(life["reason"], code="busy")
        if not life["deletable"]:
            raise FactoryError(
                f"{cid}: solo se elimina un CR sin run o con el run terminado",
                code="not_finished")
        source = find_change_request(project, cid)
        run = project.run_dir(cid)
        if _drop_worktree(project, cid):
            removed.append("worktree")
    source = _inside(source, project.cr_dir)
    if run.exists():
        shutil.rmtree(_inside(run, project.runs_dir))
        removed.append(run.relative_to(project.root).as_posix())
    source.unlink()
    removed.append(source.relative_to(project.root).as_posix())
    return {"change_id": cid, "deleted": removed}


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
    text = "\n".join(
        (run / name).read_text(encoding="utf-8")
        for name in FINDING_REPORTS
        if (run / name).is_file()
    )
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


def _verdict(fid: str, raw: object) -> dict:
    """Normaliza "ACCEPTED" o {"decision", "reason"?, "blocking"?} a una decision validada."""
    data = raw if isinstance(raw, dict) else {"decision": raw}
    verdict = str(data.get("decision") or "").strip().upper()
    if verdict not in VERDICTS:
        raise FactoryError(f"verdict invalido para {fid}: {verdict} -> {sorted(VERDICTS)}")
    reason = str(data.get("reason") or "").strip()
    if len(reason) > MAX_FINDING_TEXT:
        raise FactoryError(f"motivo de {fid} demasiado largo (max {MAX_FINDING_TEXT})")
    blocking = bool(data.get("blocking"))
    if blocking and verdict not in FIX_VERDICTS:
        raise FactoryError(
            f"{fid}: solo un finding ACCEPTED o MODIFIED se puede corregir en este ciclo"
        )
    return {"finding_id": fid, "decision": verdict, "reason": reason, "blocking": blocking}


def _added_findings(run: Path, added: list[dict], recorded_by: str) -> tuple[list[str], list[dict]]:
    """Findings del Human Reviewer -> filas HR-NN (mismo formato que el Reviewer IA) y su
    decision ACCEPTED. Valida todo antes de escribir nada."""
    path = run / HUMAN_FINDINGS
    taken = [int(n) for n in re.findall(r"\bHR-(\d{2,})\b", _read_text(path))]
    rows, decisions = [], []
    for i, item in enumerate(added, start=(max(taken) if taken else 0) + 1):
        severity = str(item.get("severity") or "").strip().upper()
        if severity not in HUMAN_SEVERITIES:
            raise FactoryError(f"severidad invalida: {severity} -> {list(HUMAN_SEVERITIES)}")
        title = " ".join(str(item.get("title") or "").split()).replace("|", "/")
        detail = " ".join(str(item.get("detail") or "").split()).replace("|", "/")
        ac = " ".join(str(item.get("ac") or "").split()).replace("|", "/") or "-"
        if not title:
            raise FactoryError("cada finding añadido necesita un titulo")
        if len(title) > 200 or len(detail) > MAX_FINDING_TEXT or len(ac) > 40:
            raise FactoryError("finding añadido demasiado largo")
        fid = f"HR-{i:02d}"
        text = f"{title}: {detail}" if detail else title
        rows.append(f"{fid} | {severity} | {ac} | - | {text} (por {recorded_by})")
        decisions.append({"finding_id": fid, "decision": "ACCEPTED",
                          "reason": "añadido por el Human Reviewer",
                          "blocking": bool(item.get("blocking"))})
    return rows, decisions


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def decide_findings(
    project: Project,
    change_id: str,
    verdicts: dict[str, object],
    recorded_by: str,
    added: list[dict] | None = None,
) -> dict:
    """Clasificacion humana por finding. Las llamadas de una ronda se ACUMULAN (la ultima
    decision por finding gana). Cada veredicto es "ACCEPTED" o {decision, reason, blocking}:
    `blocking` = "corregir en este ciclo" (escala un finding no bloqueante al Coder Fix).
    `added` son findings propios del Human Reviewer (HR-NN, aceptados por quien los añade).
    Devuelve {"decisions", "pending", "added", "path"}."""
    cid = _cid(change_id)
    role = validate_role("findings", recorded_by, project.config.gates.roles)
    added = list(added or [])
    if not verdicts and not added:
        raise FactoryError("Include at least one FINDING=VERDICT pair")
    decisions = []
    for fid, verdict in verdicts.items():
        fid = fid.strip().upper()
        if not FINDING_RE.match(fid):
            raise FactoryError(
                f"finding id invalido: {fid} (usa RF-##, HR-##, SEC-## o SEC-SCAN-##)"
            )
        decisions.append(_verdict(fid, verdict))
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
    rows, added_decisions = _added_findings(run, added, recorded_by)
    if rows:
        human = run / HUMAN_FINDINGS
        header = "" if human.is_file() else (
            f"# Findings del Human Reviewer — {cid}\n\n"
            "Formato del Reviewer: id | severidad | AC | archivo | evidencia.\n\n"
        )
        with human.open("a", encoding="utf-8") as fh:
            fh.write(header + "\n".join(rows) + "\n")
        known |= {d["finding_id"] for d in added_decisions}
    now = _now()
    path = run / "human-findings-decisions.json"
    merged: dict[str, dict] = {}
    if path.is_file():  # misma ronda: se acumula (las rondas previas ya fueron archivadas)
        for d in json.loads(path.read_text(encoding="utf-8")).get("decisions", []):
            merged[d["finding_id"]] = d
    for d in [*decisions, *added_decisions]:
        merged[d["finding_id"]] = {**d, "recorded_by": recorded_by, "role": role,
                                   "recorded_at": now}
    payload = {
        "change_id": cid,
        "gate": "findings",
        "decisions": sorted(merged.values(), key=lambda d: d["finding_id"]),
        "recorded_by": recorded_by,
        "role": role,
        "recorded_at": now,
        "note": "Los ACCEPTED/MODIFIED bloqueantes (MUST FIX/HIGH o marcados 'corregir en este "
        "ciclo' por el Human Reviewer) autorizan Coder Fix; el resto no cambia codigo.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "decisions": payload["decisions"],
        "added": [d["finding_id"] for d in added_decisions],
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
