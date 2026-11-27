"""Orchestrator de la fábrica: convierte la pipeline declarativa del starter en un
motor con human gates BLOQUEANTES, evidencia numerada y trazabilidad.

Fases (cada `advance()` ejecuta UNA fase y persiste factory-state.json):
    intake -> gate:requirements -> plan -> gate:plan -> implement+test-analysis
    -> verification -> review+security -> gate:findings -> (fix -> re-verification
    -> re-review)* -> evidence -> gate:release -> READY FOR HUMAN MERGE

Reglas duras (AGENTS.md del curso):
    - Sin veredicto humano registrado, advance() se detiene: silence is never approval.
    - Una decision solo vale si se registro DESPUES de que el run se detuvo en ese gate
      (`awaiting_gate` + `gate_opened_at`): no hay pre-aprobaciones.
    - verification FAIL jamas llega a review (R4 del curso).
    - MAX_FIX_ROUNDS por tipo (verificacion / findings); al superarlo el run queda ESCALATED.
    - Ningun camino produce merge: el estado maximo es READY FOR HUMAN MERGE.
    - Los hallazgos se consumen por ronda: tras un fix, la nueva revision exige
      decisiones nuevas (no vale la decision anterior).

Ejecutores (la fabrica no depende del proveedor; capa LLM aislada):
    FakeExecutor: plantillas deterministas. CI/desarrollo sin API key, sin recursión.
    ADKExecutor:  LlmAgents del starter via google.adk.runners (Gemini real).
"""

from __future__ import annotations

import json
import re
import subprocess
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_FIX_ROUNDS = 3
PROMPT_FIELD_CAP = 12_000

TERMINAL = {"READY FOR HUMAN MERGE", "NOT READY", "ESCALATED"}
GATE_ORDER = ("requirements", "plan", "review", "release")


class AwaitingHuman(Exception):
    def __init__(self, change_id: str, gate: str, why: str):
        super().__init__(
            f"{change_id}: STOP en human gate '{gate}' — {why}. "
            f"Registre con scripts/approve_gate.py / scripts/decide_findings.py y reanude."
        )
        self.change_id, self.gate, self.why = change_id, gate, why


class Blocked(Exception):
    pass


class TransientModelError(Exception):
    """429/503 con reintentos agotados. El run queda en su ultimo checkpoint;
    reanudar repitiendo el mismo comando --adk (curso §20)."""


_TRANSIENT_MARKERS = (
    "429", "503", "resource_exhausted", "unavailable", "high demand", "rate limit",
)


def _is_transient(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _TRANSIENT_MARKERS)


_RECO_RE = re.compile(
    r"^[\s>*_`#-]*RECOMMENDATION[\s*_`]*:[\s*_`]*(PROCEED|HUMAN_CLARIFICATION_REQUIRED)\b",
    re.I | re.M,
)


def parse_recommendation(report: str) -> str:
    """Lee SOLO la linea `RECOMMENDATION: ...` (la ultima si hay varias).
    Devuelve PROCEED | HUMAN_CLARIFICATION_REQUIRED | EMPTY | MISSING: menciones sueltas
    de STOP o HUMAN_CLARIFICATION_REQUIRED en el cuerpo ya no deciden nada."""
    if not report.strip():
        return "EMPTY"
    hits = _RECO_RE.findall(report)
    return hits[-1].upper() if hits else "MISSING"


def _retry_hint_seconds(exc: BaseException) -> int | None:
    """Extrae RetryInfo.retryDelay ('20000s') de errores 429 de genai."""
    m = re.search(r'retryDelay\D*(\d+)s', str(exc))
    return int(m.group(1)) if m else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_precise() -> str:
    """Marca con microsegundos: ordena 'gate abierto' vs 'decision registrada'."""
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: object) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


# ================================================================= ejecutores
class AgentExecutor(ABC):
    @abstractmethod
    def run_agent(self, phase: str, context: dict) -> dict: ...

    @abstractmethod
    def run_verification(self) -> dict: ...


class FakeExecutor(AgentExecutor):
    """Plantillas deterministas configurables para pruebas de comportamiento.

    ambiguous_cr:    si True, intake escala (E02).
    verify_fail:     numero de verificaciones iniciales que FALLAN (E03).
    review_report:   texto de la PRIMERA revision (p.ej. con "MUST FIX: 1 (RF-01)");
                     las revisiones posteriores quedan limpias (fix efectivo).
    """

    def __init__(self, ambiguous_cr: bool = False, verify_fail: int = 0, review_report: str = ""):
        self.ambiguous_cr = ambiguous_cr
        self.verify_fail = verify_fail
        self.review_report = review_report
        self._verify_calls = 0
        self._review_calls = 0

    def run_agent(self, phase: str, context: dict) -> dict:
        cid = context["change_id"]
        cr_text = context.get("cr_text", "")
        if phase == "intake":
            if not context.get("po_clarification") and (
                self.ambiguous_cr or re.search(r"intentionally ambiguous|ambigu", cr_text, re.I)
            ):
                return {
                    "recommendation": "HUMAN_CLARIFICATION_REQUIRED",
                    "report": f"# Intake {cid}\n\nSTOP: el CR no define politica; "
                    "requiere decision del Product Owner.\n\n"
                    "RECOMMENDATION: HUMAN_CLARIFICATION_REQUIRED\n",
                }
            return {
                "recommendation": "PROCEED",
                "report": f"# Intake {cid}\n\nACs identificados, "
                "sin ambiguedad bloqueante.\n\nRECOMMENDATION: PROCEED\n",
            }
        if phase == "review":
            self._review_calls += 1
            text = (
                self.review_report
                if (self._review_calls == 1 and self.review_report)
                else f"# Review {cid}\n\nMUST FIX: 0\nSHOULD FIX: 0\n"
            )
            return {"report": text}
        content = {
            "plan": (
                f"# Plan {cid}\n\n| task | files | tests |\n|---|---|---|\n"
                "| T1 | campusflow/service.py | tests/product/test_service.py |\n\n"
                "Verificacion: pytest + ruff.\n\nRECOMMENDATION: PROCEED\n"
            ),
            "implement": f"# Coder report {cid}\n\nfiles_changed: campusflow/service.py\n",
            "test_analysis": f"# Test analysis {cid}\n\nboundary/negativos/regresion cubiertos.\n",
            "security": f"# Security {cid}\n\nHIGH: 0\nMEDIUM: 0\n",
            "fix": f"# Fix {cid}\n\nSolo findings autorizados corregidos.\n",
            "evidence": f"# Evidence {cid}\n",
        }
        return {"report": content.get(phase, f"# {phase} {cid}\n"), "recommendation": "PROCEED"}

    def run_verification(self) -> dict:
        self._verify_calls += 1
        if self._verify_calls <= self.verify_fail:
            return {
                "ok": False,
                "results": [
                    {
                        "command": "pytest (fake)",
                        "returncode": 1,
                        "stdout": "2 failed, 10 passed",
                        "stderr": "",
                    }
                ],
            }
        return {
            "ok": True,
            "results": [
                {
                    "command": "pytest (fake)",
                    "returncode": 0,
                    "stdout": "12 passed in 0.10s",
                    "stderr": "",
                },
                {
                    "command": "ruff (fake)",
                    "returncode": 0,
                    "stdout": "All checks passed!",
                    "stderr": "",
                },
            ],
        }


class ADKExecutor(AgentExecutor):
    """Cada fase corre en una sesion ADK NUEVA, con contexto reconstruido desde
    artefactos/diff — nunca desde la conversacion de otro agente (independencia R5).
    Import perezoso: la fabrica funciona sin google-adk (modo fake)."""

    AGENT_FOR_PHASE = {
        "intake": "intake_agent",
        "plan": "planner_agent",
        "implement": "coder_agent",
        "test_analysis": "test_analyst_agent",
        "review": "reviewer_agent",
        "security": "security_agent",
        "fix": "coder_agent",
        "evidence": "evidence_agent",
    }

    def __init__(self, backoff_base: float = 3.0, max_retries: int = 5):
        self.backoff_base = backoff_base
        self.max_retries = max_retries

    def run_agent(self, phase: str, context: dict) -> dict:
        import asyncio

        from google.adk.runners import InMemoryRunner
        from google.genai import types

        from . import agents as A
        from .tools import select_context

        agent = getattr(A, self.AGENT_FOR_PHASE[phase])
        # Context engineering (§12): solo lo relevante, con manifiesto auditable en el run.
        context = {**context, "selected_context": select_context(context["change_id"])["text"]}
        prompt = self._prompt(phase, context)

        async def _run() -> str:
            runner = InMemoryRunner(agent=agent, app_name=f"factory_{phase}")
            session = await runner.session_service.create_session(
                app_name=runner.app_name, user_id=context["change_id"]
            )
            new_message = types.Content(role="user", parts=[types.Part(text=prompt)])
            text = ""
            async for event in runner.run_async(
                user_id=session.user_id, session_id=session.id, new_message=new_message
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    text = event.content.parts[-1].text or ""
            return text

        def _call() -> str:
            return asyncio.run(_run())

        report = self._call_with_backoff(_call, phase, context["change_id"])
        return {
            "report": report or f"# {phase} {context['change_id']} (respuesta vacía)",
            # vacia o sin linea RECOMMENDATION => el run escala (nunca PROCEED por defecto)
            "recommendation": parse_recommendation(report or ""),
        }

    def _call_with_backoff(self, fn, phase: str, change_id: str):
        """§20: exponential backoff + jitter ante 429/503/high-demand.

        Reintentos quedan registrados en factory-state.json (metrica §19.1).
        Agotados los reintentos: TransientModelError; el run NO pierde checkpoint.
        """
        import random
        import time

        max_retries = self.max_retries
        attempt = 0
        while True:
            try:
                result = fn()
            except Exception as exc:
                if not _is_transient(exc):
                    raise
                hint = _retry_hint_seconds(exc)
                if hint is not None and hint > 600:
                    # cuota diaria: reintentar YA gastaria quota sin posibilidad;
                    # checkpoint intacto + mensaje con ventana de resume (§20)
                    self._record_retries(change_id, phase, attempt, exhausted=True)
                    raise TransientModelError(
                        f"{phase}: cuota free-tier; reintentar en ~{hint // 60} min O "
                        "cambiar FACTORY_MODEL (pool de quota distinto, p. ej. "
                        "gemini-3.5-flash-lite); run resumible"
                    ) from exc
                if attempt >= max_retries:
                    self._record_retries(change_id, phase, attempt, exhausted=True)
                    raise TransientModelError(
                        f"{phase}: {max_retries} reintentos agotados; el run sigue en su "
                        "ultimo checkpoint — reanudar con la misma linea --adk"
                    ) from exc
                attempt += 1
                delay = self.backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 2)
                print(f"  [llm] {phase}: transitorio ({type(exc).__name__}), "
                      f"intento {attempt}/{max_retries} en {delay:.1f}s")
                time.sleep(delay)
                continue
            # exito: los reintentos que costo tambien cuentan en la metrica (§19.1)
            self._record_retries(change_id, phase, attempt, exhausted=False)
            return result

    @staticmethod
    def _record_retries(change_id: str, phase: str, n: int, exhausted: bool) -> None:
        """Persiste la metrica de rate-limit retries en factory-state.json."""
        if n == 0 and not exhausted:
            return
        run_dir = REPO_ROOT / "runs" / str(change_id).strip().upper()
        if not _state_path(run_dir).is_file():
            return
        prior = json.loads(_state_path(run_dir).read_text(encoding="utf-8")).get(
            "llm_retries", 0
        )
        _update_state(
            run_dir,
            llm_retries=prior + n,
            last_llm_note=f"{phase}: {n} backoff retries"
            + (" — EXHAUSTED (run resumable)" if exhausted else ""),
        )

    def run_verification(self) -> dict:
        from .tools import run_baseline_verification

        return run_baseline_verification()

    @staticmethod
    def _prompt(phase: str, context: dict) -> str:
        bits = [f"Change ID: {context['change_id']}"]
        for key in (
            "cr_text",
            "po_clarification",
            "selected_context",
            "intake_report",
            "implementation_plan",
            "git_diff",
            "verification_summary",
            "prior_findings",
            "accepted_blocking_findings",
        ):
            if context.get(key):
                text = str(context[key])
                if len(text) > PROMPT_FIELD_CAP:  # truncado VISIBLE: el agente sabe que falta
                    text = (
                        text[:PROMPT_FIELD_CAP]
                        + f"\n...[TRUNCATED {len(text) - PROMPT_FIELD_CAP} chars; "
                        "use the read tools / inspect_git_diff for the rest]"
                    )
                bits.append(f"{key.upper()}:\n{text}")
        return "\n\n".join(bits)


# ================================================================= estado
def _state_path(run_dir: Path) -> Path:
    return run_dir / "factory-state.json"


def _update_state(run_dir: Path, **kw) -> dict:
    state = json.loads(_state_path(run_dir).read_text(encoding="utf-8"))
    state.update(kw)
    state.setdefault("transitions", []).append({"status": state["status"], "at": _now()})
    _state_path(run_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _load_state(run_dir: Path) -> dict:
    return json.loads(_state_path(run_dir).read_text(encoding="utf-8"))


def _gate_decision(run_dir: Path, gate: str, state: dict) -> dict | None:
    """Decision humana VALIDA para `gate`: solo si la fabrica esta detenida en ese gate y
    la decision se registro DESPUES de abrirse (nunca pre-aprobaciones ni decisiones
    viejas de otra ronda). Lo demas se ignora: silence is never approval."""
    path = run_dir / f"human-{gate}-decision.json"
    if not path.is_file() or state.get("awaiting_gate") != gate:
        return None
    decision = json.loads(path.read_text(encoding="utf-8"))
    opened, recorded = (
        _parse_ts(state.get("gate_opened_at")),
        _parse_ts(decision.get("recorded_at")),
    )
    if opened is None or recorded is None or recorded < opened:
        return None
    return decision


def _open_gate(run_dir: Path, change_id: str, gate: str, why: str, state: dict, **kw) -> None:
    """Detiene el run en `gate`. La marca de apertura se conserva si ya estaba abierto
    (reanudar sin decision no invalida una decision registrada entre dos advance)."""
    if state.get("awaiting_gate") != gate:
        kw.update(awaiting_gate=gate, gate_opened_at=_now_precise())
    if kw:
        _update_state(run_dir, **kw)
    raise AwaitingHuman(change_id, gate, why)


def _escalate(run_dir: Path, change_id: str, reason: str, **kw) -> dict:
    """ESCALATED + evidencia consolidada (factory-run.json, resumen, trazabilidad) para que
    un escalamiento tambien sea exportable y auditable."""
    kw.setdefault("awaiting_gate", "")
    state = _update_state(
        run_dir, status="ESCALATED", stop_reason=reason, escalated_at=_now(), **kw
    )
    _write_consolidated_evidence(change_id, state, run_dir)
    return state


def _write_consolidated_evidence(change_id: str, state: dict, run_dir: Path) -> dict:
    run_json = build_factory_run(change_id, state, run_dir)
    (run_dir / "factory-run.json").write_text(json.dumps(run_json, indent=2), encoding="utf-8")
    (run_dir / "14-evidence-summary.md").write_text(
        build_evidence_summary(run_json, run_dir), encoding="utf-8"
    )
    (run_dir / "traceability.md").write_text(
        build_traceability(run_json, run_dir), encoding="utf-8"
    )
    return run_json


# Artefactos de decision humana con el nombre exacto del instructivo (§17). Los
# human-<gate>-decision.json internos siguen siendo la fuente que lee el motor.
GATE_ARTIFACT = {
    "requirements": "02-human-requirement-decision.md",
    "plan": "04-human-plan-approval.md",
}


def _write_gate_artifact(run_dir: Path, gate: str, decision: dict, extra: str = "") -> None:
    """Registro legible y auditable de una decision humana (quien, que, por que, cuando)."""
    name = GATE_ARTIFACT.get(gate)
    if name is None:
        return
    (run_dir / name).write_text(
        "\n".join(
            [
                f"# Human decision — gate {gate}",
                "",
                f"- Decision: **{decision.get('decision', '?')}**",
                f"- Recorded by: {decision.get('recorded_by', 'unknown')}",
                f"- Recorded at: {decision.get('recorded_at', 'unknown')}",
                f"- Reason: {decision.get('reason', '')}",
                "",
                extra,
            ]
        ),
        encoding="utf-8",
    )


def _apply_gate_decision(
    run_dir: Path, change_id: str, gate: str, decision: dict, state: dict
) -> dict:
    """Consume la decision: cierra el gate y la registra en human_gates.
    REJECTED/DEFERRED escalan (requirements, plan, review); no hay continuacion automatica."""
    _write_gate_artifact(run_dir, gate, decision)
    gates = {**state["human_gates"], gate: decision["decision"]}
    if decision["decision"] in {"APPROVED", "MODIFIED"}:
        return _update_state(run_dir, awaiting_gate="", human_gates=gates)
    _escalate(run_dir, change_id, f"gate {gate}: {decision['decision']}", human_gates=gates)
    raise AwaitingHuman(change_id, gate, f"decision {decision['decision']}: intervenga un humano")


def _archive(run_dir: Path, name: str, round_no: int) -> None:
    src = run_dir / name
    if src.is_file():
        stem, dot, ext = name.rpartition(".")
        src.rename(run_dir / f"{stem}-round{round_no}{dot}{ext}")


def _try_resume_escalation(run_dir: Path, state: dict) -> dict | None:
    """Un ESCALATED por ambiguedad (intake/planner) se reanuda SOLO cuando el humano
    registra una decision APPROVED/MODIFIED POSTERIOR al escalamiento (curso §11.3:
    STOP hasta recibir decision humana). Rechazos de gate (`gate ...`) no se reanudan.

    La aclaracion SOLO reanuda: se archiva y el intake/plan regenerados vuelven a pasar
    por su gate con una decision nueva (nadie aprueba un artefacto que no vio)."""
    reason = str(state.get("stop_reason", ""))
    resume = {"intake:": ("requirements", "INTAKE_PENDING"), "planner:": ("plan", "PLAN_PENDING")}
    for prefix, (gate, target) in resume.items():
        if not reason.startswith(prefix):
            continue
        decision = _gate_decision(run_dir, gate, state)
        if decision and decision.get("decision") in {"APPROVED", "MODIFIED"}:
            n = len(list(run_dir.glob(f"human-{gate}-decision-clarification*.json")))
            (run_dir / f"human-{gate}-decision.json").rename(
                run_dir / f"human-{gate}-decision-clarification{n or ''}.json"
            )
            return _update_state(
                run_dir,
                status=target,
                stop_reason="",
                awaiting_gate="",
                planned=False,
                po_clarification=str(decision.get("reason", ""))[:2000],
            )
    return None


def _commit_product_changes(change_id: str, fix_reason: str) -> str:
    """Git evidence: comitea cambios de producto SOLO en la rama factory/<CR>.

    Sin repo git o sin rama de run => no comitea (nunca en main: regla dura).
    Autoria 'Agentic Software Factory': commit de maquina que espera review humano.
    Retorna hash corto o ''.
    """
    if _git(["rev-parse", "--verify", "HEAD"]).returncode != 0:
        return ""
    branch = f"factory/{change_id}"
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if current != branch:
        if _git(["rev-parse", "--verify", branch]).returncode != 0:
            return ""
        _git(["checkout", "-q", branch])
    for path in ("campusflow", "tests/product"):
        if (REPO_ROOT / path).exists():
            _git(["add", path])
    if _git(["diff", "--cached", "--quiet"]).returncode == 0:
        return ""
    msg = f"factory({change_id}): " + (
        "apply accepted findings" if fix_reason else "implement approved plan"
    )
    r = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Agentic Software Factory",
            "-c",
            "user.email=factory@local",
            "commit",
            "-q",
            "-m",
            msg,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return ""
    return _git(["rev-parse", "--short", "HEAD"]).stdout.strip()


def _last_verification_file(run_dir: Path) -> str:
    return (
        "12-re-verification.txt"
        if (run_dir / "12-re-verification.txt").is_file()
        else "07-verification.txt"
    )


def _create_run_branch(change_id: str, state: dict) -> dict:
    branch = f"factory/{change_id}"
    if _git(["rev-parse", "--verify", branch]).returncode != 0:
        _git(["checkout", "-b", branch])
    return {"branch": branch}


# ================================================================= avance
def advance(change_id: str, executor: AgentExecutor | None = None) -> dict:
    executor = executor or FakeExecutor()
    change_id = change_id.strip().upper()
    run_dir = REPO_ROOT / "runs" / change_id
    if not _state_path(run_dir).is_file():
        raise SystemExit(
            f"No existe run {change_id}. Ejecute: python scripts/start_run.py {change_id}"
        )
    state = json.loads(_state_path(run_dir).read_text(encoding="utf-8"))
    status = state["status"]
    if status == "ESCALATED":
        state = _try_resume_escalation(run_dir, state) or state
        status = state["status"]
    if status in TERMINAL:
        return state
    cr_text = _read(run_dir / "00-change-request.md")
    clarification = state.get("po_clarification", "")

    # ---- 1 INTAKE
    if status == "INTAKE_PENDING":
        out = executor.run_agent(
            "intake",
            {"change_id": change_id, "cr_text": cr_text, "po_clarification": clarification},
        )
        (run_dir / "01-intake-report.md").write_text(out["report"], encoding="utf-8")
        reco = out.get("recommendation")
        if reco != "PROCEED":
            why = (
                "requiere clarificacion del Product Owner"
                if reco == "HUMAN_CLARIFICATION_REQUIRED"
                else f"respuesta del modelo sin RECOMMENDATION valida ({reco})"
            )
            _escalate(
                run_dir,
                change_id,
                f"intake: {why}",
                awaiting_gate="requirements",
                gate_opened_at=_now_precise(),
            )
            raise AwaitingHuman(
                change_id, "requirements", f"intake: {why}; resuelva con approve_gate requirements"
            )
        state = _update_state(run_dir, status="REQUIREMENTS_GATE")
        return advance(change_id, executor)

    # ---- 1b gate requirements (intake ya hecho: no se repite al reanudar)
    if status == "REQUIREMENTS_GATE":
        decision = _gate_decision(run_dir, "requirements", state)
        if decision is None:
            _open_gate(
                run_dir, change_id, "requirements", "el PO debe validar interpretación/alcance",
                state,
            )
        state = _apply_gate_decision(run_dir, change_id, "requirements", decision, state)
        _update_state(run_dir, status="PLAN_PENDING", planned=False)
        return advance(change_id, executor)

    # ---- 2 PLANNER + gate plan (el Coder bloqueado hasta APPROVED)
    if status == "PLAN_PENDING":
        # El plan se genera UNA vez: al reanudar tras la decision NO se regenera
        # (el Tech Lead aprobo ESTE plan, no uno nuevo).
        if not state.get("planned"):
            out = executor.run_agent(
                "plan",
                {
                    "change_id": change_id,
                    "cr_text": cr_text,
                    "intake_report": _read(run_dir / "01-intake-report.md"),
                    "po_clarification": clarification,
                },
            )
            (run_dir / "03-implementation-plan.md").write_text(out["report"], encoding="utf-8")
            reco = out.get("recommendation")
            if reco != "PROCEED":
                why = (
                    "ambiguedad residual"
                    if reco == "HUMAN_CLARIFICATION_REQUIRED"
                    else f"respuesta del modelo sin RECOMMENDATION valida ({reco})"
                )
                _escalate(
                    run_dir,
                    change_id,
                    f"planner: {why}",
                    awaiting_gate="plan",
                    gate_opened_at=_now_precise(),
                )
                raise AwaitingHuman(change_id, "plan", f"planner: {why}")
            state = _update_state(run_dir, planned=True)
        decision = _gate_decision(run_dir, "plan", state)
        if decision is None:
            _open_gate(
                run_dir, change_id, "plan", "el Coder permanece BLOQUEADO hasta plan APPROVED",
                state,
            )
        state = _apply_gate_decision(run_dir, change_id, "plan", decision, state)
        _update_state(run_dir, status="IMPLEMENT_PENDING", **_create_run_branch(change_id, state))
        return advance(change_id, executor)

    # ---- 3 CODER + TEST ANALYST
    if status == "IMPLEMENT_PENDING":
        fix_reason = state.get("fix_reason", "")
        ctx = {
            "change_id": change_id,
            "cr_text": cr_text,
            "implementation_plan": _read(run_dir / "03-implementation-plan.md"),
            # el Coder Fix recibe QUE corregir, no solo los ids
            "accepted_blocking_findings": (
                fix_reason + "\n\n" + state.get("fix_details", "") if fix_reason else ""
            ),
            "verification_summary": (
                _read(run_dir / _last_verification_file(run_dir))
                if fix_reason == "verification FAIL"
                else ""
            ),
        }
        phase = "fix" if fix_reason else "implement"
        out = executor.run_agent(phase, ctx)
        target = "11-fix-report.md" if fix_reason else "05-coder-report.md"
        commit = _commit_product_changes(change_id, fix_reason)
        diff_stat = _git(["diff", "main...HEAD", "--stat"]).stdout if commit else ""
        report = out["report"]
        if commit:
            report += (
                f"\n\n## Git evidence\n\n- commit: `{commit}` "
                f"(rama {state.get('branch', 'factory/' + change_id)})\n```text\n{diff_stat}```\n"
            )
        (run_dir / target).write_text(report, encoding="utf-8")
        if not fix_reason:
            out2 = executor.run_agent(
                "test_analysis",
                {
                    "change_id": change_id,
                    "cr_text": cr_text,
                    "implementation_plan": ctx["implementation_plan"],
                    "git_diff": _git(["diff", "main...HEAD"]).stdout,
                },
            )
            (run_dir / "06-test-analysis.md").write_text(out2["report"], encoding="utf-8")
        _update_state(
            run_dir,
            status="VERIFY_PENDING",
            fix_reason="",
            fix_details="",
            last_commit=commit or state.get("last_commit", ""),
        )
        return advance(change_id, executor)

    # ---- 4 VERIFICATION determinista (FAIL nunca llega a review)
    if status == "VERIFY_PENDING":
        v = executor.run_verification()
        round_no = state.get("iterations", 0)
        (
            run_dir / "07-verification.txt" if round_no == 0 else run_dir / "12-re-verification.txt"
        ).write_text(json.dumps(v, indent=2), encoding="utf-8")
        tests, failed, lint = parse_verification(v)
        verdict = "PASS" if v.get("ok") else "FAIL"
        ver = {"status": verdict, "tests": tests, "failed": failed, "lint": lint}
        if verdict == "FAIL":
            verify_rounds = state.get("verify_rounds", 0)
            if verify_rounds >= MAX_FIX_ROUNDS:
                _escalate(
                    run_dir,
                    change_id,
                    f"verification FAIL tras {verify_rounds} rondas de fix (tope {MAX_FIX_ROUNDS})",
                    verification=ver,
                )
                raise Blocked(f"{change_id}: verification FAIL persistente -> ESCALATED a humano")
            _update_state(
                run_dir,
                status="IMPLEMENT_PENDING",
                iterations=round_no + 1,
                verify_rounds=verify_rounds + 1,
                verification=ver,
                fix_reason="verification FAIL",
            )
            return advance(change_id, executor)
        _update_state(run_dir, status="REVIEW_PENDING", verification=ver)
        return advance(change_id, executor)

    # ---- 5 REVIEWER + SECURITY independientes + gate findings
    if status == "REVIEW_PENDING":
        rounds = state.get("iterations", 0)
        # Los reportes se generan UNA vez por ronda: al reanudar tras la decision humana
        # NO se re-ejecutan (el humano clasifico ESTOS findings, no unos nuevos).
        fresh = not (
            state.get("reviewed_round") == rounds and (run_dir / "08-review-report.md").is_file()
        )
        if fresh:
            prior_findings = ""
            re_review = (run_dir / "08-review-report.md").is_file()
            if re_review:  # re-review: conservar la ronda anterior, no sobrescribirla
                prior_findings = (
                    _read(run_dir / "08-review-report.md")
                    + "\n"
                    + _read(run_dir / "09-security-report.md")
                    + "\nFIX AUTORIZADO: "
                    + str(state.get("last_fix_reason", ""))
                )
                n = len(list(run_dir.glob("08-review-report-round*.md")))
                _archive(run_dir, "08-review-report.md", n)
                _archive(run_dir, "09-security-report.md", n)
            diff = _git(["diff", "main...HEAD"]).stdout
            review_ctx = {
                "change_id": change_id,
                "cr_text": cr_text,
                "implementation_plan": _read(run_dir / "03-implementation-plan.md"),
                "git_diff": diff,
                "verification_summary": _read(run_dir / _last_verification_file(run_dir)),
                "prior_findings": prior_findings,
            }
            review = executor.run_agent("review", review_ctx)  # sesion propia
            (run_dir / "08-review-report.md").write_text(review["report"], encoding="utf-8")
            sec = executor.run_agent(
                "security",
                {
                    "change_id": change_id,
                    "cr_text": cr_text,
                    "git_diff": diff,
                    "prior_findings": prior_findings,
                },
            )  # sesion propia distinta
            from .scanners import run_security_scans

            scans = run_security_scans(REPO_ROOT)
            (run_dir / "09-security-report.md").write_text(
                sec["report"]
                + "\n\n## Escaneos deterministas\n\n```json\n"
                + json.dumps(scans, indent=2)
                + "\n```\n",
                encoding="utf-8",
            )
            if re_review:
                (run_dir / "13-re-review.md").write_text(
                    f"# Re-review — {change_id} (ronda {rounds})\n\n"
                    "Reviewer y Security re-chequean los findings autorizados y buscan "
                    "regresiones/riesgos nuevos introducidos por el fix.\n\n"
                    "## Reviewer\n\n" + review["report"] + "\n\n## Security\n\n"
                    + sec["report"] + "\n",
                    encoding="utf-8",
                )
            counts = count_findings(review["report"] + "\n" + sec["report"])
            review_status = "PASS" if counts["must_fix"] == 0 else "CHANGES_REQUESTED"
            security_status = "BLOCKED" if (counts["high"] > 0 or scans["blocked"]) else "PASS"
            state = _update_state(
                run_dir,
                reviewed_round=rounds,
                review={
                    "status": review_status,
                    **{k: counts[k] for k in ("must_fix", "should_fix")},
                },
                security={
                    "status": security_status,
                    "high": counts["high"],
                    "medium": counts["medium"],
                },
            )
        decision = _gate_decision(run_dir, "review", state)
        if decision is None:
            _open_gate(
                run_dir,
                change_id,
                "review",
                "clasifique cada finding (ACCEPTED/REJECTED/MODIFIED/DEFERRED)",
                state,
            )
        blocking = (
            accepted_blocking(run_dir, state)
            if decision["decision"] in {"APPROVED", "MODIFIED"}
            else []
        )
        _record_review_decisions(run_dir, rounds, decision, blocking, state)
        state = _apply_gate_decision(run_dir, change_id, "review", decision, state)
        if blocking:
            review_rounds = state.get("review_rounds", 0)
            if review_rounds >= MAX_FIX_ROUNDS:
                _escalate(
                    run_dir,
                    change_id,
                    f"findings bloqueantes aceptados tras {review_rounds} rondas de fix "
                    f"(tope {MAX_FIX_ROUNDS}): " + ", ".join(blocking),
                )
                raise Blocked(f"{change_id}: findings bloqueantes persistentes -> ESCALATED")
            _archive(run_dir, "human-review-decision.json", rounds)
            _archive(run_dir, "human-findings-decisions.json", rounds)
            reason = "findings bloqueantes aceptados: " + ", ".join(blocking)
            segments = finding_segments(
                _read(run_dir / "08-review-report.md")
                + "\n"
                + _read(run_dir / "09-security-report.md")
            )
            details = "\n".join(segments.get(fid, fid).strip() for fid in blocking)
            _update_state(
                run_dir,
                status="IMPLEMENT_PENDING",
                iterations=rounds + 1,
                review_rounds=review_rounds + 1,
                fix_reason=reason,
                fix_details=details[:8000],
                last_fix_reason=reason,
            )
            return advance(change_id, executor)
        _update_state(run_dir, status="EVIDENCE_PENDING")
        return advance(change_id, executor)

    # ---- 6 EVIDENCIA + gate release
    if status == "EVIDENCE_PENDING":
        state = _load_state(run_dir)
        _write_consolidated_evidence(change_id, state, run_dir)
        decision = _gate_decision(run_dir, "release", state)
        if decision is None:
            _open_gate(
                run_dir,
                change_id,
                "release",
                "Release Manager decide con evidencia consolidada",
                state,
                **({} if state.get("evidence") == "CONSOLIDATED" else {"evidence": "CONSOLIDATED"}),
            )
        verdict = decision["decision"]
        final = {"APPROVED": "READY FOR HUMAN MERGE", "REJECTED": "NOT READY"}.get(
            verdict, "ESCALATED"
        )
        state = _update_state(
            run_dir,
            status=final,
            evidence="CONSOLIDATED",
            awaiting_gate="",
            human_gates={**state["human_gates"], "release": verdict},
            release_decision=decision,
            **({"stop_reason": f"gate release: {verdict}"} if final == "ESCALATED" else {}),
        )
        (run_dir / "15-release-decision.json").write_text(
            json.dumps({**decision, "final_status": final}, indent=2), encoding="utf-8"
        )
        (run_dir / "factory-run.json").write_text(
            json.dumps(build_factory_run(change_id, state, run_dir), indent=2), encoding="utf-8"
        )
        return state

    raise Blocked(f"{change_id}: estado {status} requiere accion humana explicita")


# ================================================================= parsing/consolidacion
def parse_verification(v: dict) -> tuple[int, int, str]:
    out = " ".join(r.get("stdout", "") + r.get("stderr", "") for r in v.get("results", []))
    tests = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    m2 = re.search(r"(\d+) failed", out)
    tests += int(m2.group(1)) if m2 else 0
    ruff_ok = any(
        "ruff" in r.get("command", "") and r.get("returncode", 1) == 0 for r in v.get("results", [])
    )
    lint = "PASS" if ruff_ok or "All checks passed" in out else "FAIL"
    return tests, (int(m2.group(1)) if m2 else 0), lint


def count_findings(text: str) -> dict:
    def num(pattern: str) -> int:
        m = re.search(pattern, text, re.I)
        return int(m.group(1)) if m else 0

    return {
        "must_fix": num(r"MUST FIX:?\s*(\d+)"),
        "should_fix": num(r"SHOULD FIX:?\s*(\d+)"),
        "high": num(r"HIGH:?\s*(\d+)"),
        "medium": len(re.findall(r"\bMEDIUM\b", text)),
    }


FINDING_ID_RE = re.compile(r"\b(?:RF|SEC)-\d{2,}\b")
SEVERITY_RE = re.compile(r"\b(?:MUST FIX|SHOULD FIX|OPTIONAL|CRITICAL|HIGH|MEDIUM|LOW)\b", re.I)
BLOCKING_SEVERITIES = {"MUST FIX", "CRITICAL", "HIGH"}


def finding_segments(text: str) -> dict[str, str]:
    """finding id -> texto desde su primera aparicion hasta el siguiente finding distinto."""
    hits: list[tuple[str, int]] = []
    seen: set[str] = set()
    for m in FINDING_ID_RE.finditer(text):
        if m.group(0) not in seen:
            seen.add(m.group(0))
            hits.append((m.group(0), m.start()))
    return {
        fid: text[start : hits[i + 1][1] if i + 1 < len(hits) else len(text)]
        for i, (fid, start) in enumerate(hits)
    }


def finding_severities(run_dir: Path) -> dict[str, str]:
    """Severidad de cada finding segun los reportes de la ronda actual (misma linea del id
    primero; si no, dentro de su segmento). Sin severidad detectable => no se incluye."""
    text = _read(run_dir / "08-review-report.md") + "\n" + _read(run_dir / "09-security-report.md")
    out: dict[str, str] = {}
    for fid, segment in finding_segments(text).items():
        line_start = text.find(segment)
        line_end = text.find("\n", line_start)
        line = text[text.rfind("\n", 0, line_start) + 1 : line_end if line_end != -1 else None]
        m = SEVERITY_RE.search(line) or SEVERITY_RE.search(segment)
        if m:
            out[fid] = m.group(0).upper()
    return out


def findings_decisions(run_dir: Path, state: dict) -> dict:
    """Decisiones por finding VALIDAS: registradas con el gate review abierto (no antes)."""
    path = run_dir / "human-findings-decisions.json"
    if not path.is_file() or state.get("awaiting_gate") != "review":
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    opened, recorded = _parse_ts(state.get("gate_opened_at")), _parse_ts(data.get("recorded_at"))
    if opened is None or recorded is None or recorded < opened:
        return {}
    return data


def accepted_blocking(run_dir: Path, state: dict) -> list[str]:
    """Findings que el humano ACEPTO/MODIFICO Y que son bloqueantes (MUST FIX / HIGH).
    Un SHOULD FIX aceptado NO dispara un ciclo de fix; sin severidad parseable se trata
    como bloqueante (conservador)."""
    data = findings_decisions(run_dir, state)
    if not data:
        return []  # sin decision explicita, ningun finding se auto-aplica (Gate 2 real)
    severities = finding_severities(run_dir)
    return [
        d["finding_id"]
        for d in data.get("decisions", [])
        if d.get("decision") in {"ACCEPTED", "MODIFIED"}
        and severities.get(d["finding_id"], "MUST FIX") in BLOCKING_SEVERITIES
    ]


def _record_review_decisions(
    run_dir: Path, round_no: int, decision: dict, blocking: list[str], state: dict
) -> None:
    """10-human-review-decisions.md (§17): decisiones por finding de cada ronda, append-only."""
    path = run_dir / "10-human-review-decisions.md"
    data = findings_decisions(run_dir, state)
    severities = finding_severities(run_dir)
    lines = [
        f"## Round {round_no} — gate review: {decision.get('decision')}",
        "",
        f"- Recorded by: {decision.get('recorded_by', 'unknown')} "
        f"at {decision.get('recorded_at', 'unknown')}",
        f"- Reason: {decision.get('reason', '')}",
        f"- Blocking accepted (authorize Coder Fix): {', '.join(blocking) or 'none'}",
        "",
        "| Finding | Severity | Human decision |",
        "|---|---|---|",
        *[
            f"| {d['finding_id']} | {severities.get(d['finding_id'], 'unknown')} "
            f"| {d['decision']} |"
            for d in data.get("decisions", [])
        ],
        "",
    ]
    header = "" if path.is_file() else f"# Human review decisions — {run_dir.name}\n\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(header + "\n".join(lines) + "\n")


def build_factory_run(change_id: str, state: dict, run_dir: Path) -> dict:
    """Shape del curso: contadores de evidencia real, nunca opinion del LLM."""
    ver = state.get("verification", {})
    if isinstance(ver, str):
        ver = {"status": ver, "tests": 0, "failed": 0, "lint": "NOT_RUN"}
    return {
        "change_id": change_id,
        "status": "AWAITING RELEASE DECISION"
        if state["status"] == "EVIDENCE_PENDING"
        else state["status"],
        "requirements": state["human_gates"].get("requirements", "PENDING"),
        "plan": state["human_gates"].get("plan", "PENDING"),
        "implementation": "COMPLETE"
        if (run_dir / "05-coder-report.md").exists()
        else "NOT_STARTED",
        "test_analysis": "COMPLETE"
        if (run_dir / "06-test-analysis.md").exists()
        else "NOT_STARTED",
        "verification": {
            "status": ver.get("status", "NOT_RUN"),
            "tests": ver.get("tests", 0),
            "failed": ver.get("failed", 0),
            "lint": ver.get("lint", "NOT_RUN"),
        },
        "review": state.get("review", {"status": "NOT_RUN", "must_fix": 0, "should_fix": 0}),
        "security": state.get("security", {"status": "NOT_RUN", "high": 0, "medium": 0}),
        "evidence": {
            "status": state.get("evidence", "PENDING"),
            "summary": "14-evidence-summary.md",
            "traceability": "traceability.md",
            "run_summary": "factory-run.json",
        },
        "human_gates": {
            "product_owner": state["human_gates"].get("requirements", "PENDING"),
            "tech_lead": state["human_gates"].get("plan", "PENDING"),
            "reviewer": state["human_gates"].get("review", "PENDING"),
            "release_manager": state["human_gates"].get("release", "PENDING"),
        },
        "iterations": state.get("iterations", 0),
        "traceability_gaps": traceability_gaps(compute_traceability(run_dir)),
        "history": state.get("transitions", []),
    }


def build_evidence_summary(run: dict, run_dir: Path) -> str:
    present = sorted(p.name for p in run_dir.iterdir() if p.is_file())
    return (
        "\n".join(
            [
                f"# Evidence summary — {run['change_id']}",
                "",
                f"- Estado: **{run['status']}**",
                f"- Verification: {run['verification']}",
                f"- Review: {run['review']}",
                f"- Security: {run['security']}",
                f"- Gates humanos: {run['human_gates']}",
                f"- Iteraciones fix: {run['iterations']}",
                f"- Traceability gaps ({UNMAPPED}): {run.get('traceability_gaps', 0)}",
                f"- Artefactos ({len(present)}): {', '.join(present)}",
                "",
                "> Generado por factory_app/orchestrator desde evidencia real del run.",
                "",
            ]
        )
        + "\n"
    )


AC_RE = re.compile(r"AC-[A-Za-z0-9]+\.\d+")
UNMAPPED = "UNMAPPED"


def compute_traceability(run_dir: Path) -> list[dict]:
    """Req/AC -> Task -> Code -> Test -> Finding -> Human decision, solo con vinculos
    EVIDENCIABLES (el plan cita el AC; el diff contiene el archivo; un test cita el AC;
    un finding cita el AC). Si no hay vinculo => UNMAPPED, nunca relleno."""
    acs = sorted(set(AC_RE.findall(_read(run_dir / "00-change-request.md"))))
    plan_lines = _read(run_dir / "03-implementation-plan.md").splitlines()
    diff_files = {
        f
        for f in _git(["diff", "--name-only", "main...HEAD"]).stdout.split()
        if f.startswith(("campusflow/", "tests/product/"))
    }
    tests_by_ac: dict[str, list[str]] = {}
    tests_dir = REPO_ROOT / "tests" / "product"
    for tf in sorted(tests_dir.glob("test_*.py")) if tests_dir.is_dir() else []:
        for chunk in re.split(r"(?m)^(?=def test_|    def test_)", tf.read_text(encoding="utf-8")):
            name = re.match(r"\s*def (test_\w+)", chunk)
            if not name:
                continue
            for ac in set(AC_RE.findall(chunk)):
                tests_by_ac.setdefault(ac, []).append(f"{tf.name}::{name.group(1)}")
    reports = (
        _read(run_dir / "08-review-report.md") + "\n" + _read(run_dir / "09-security-report.md")
    )
    segments = finding_segments(reports)
    dec_path = run_dir / "human-findings-decisions.json"
    decisions = (
        {
            d["finding_id"]: d["decision"]
            for d in json.loads(dec_path.read_text(encoding="utf-8")).get("decisions", [])
        }
        if dec_path.is_file()
        else {}
    )
    rows = []
    for ac in acs:
        cited = [line for line in plan_lines if ac in line]
        tasks = sorted({t for line in cited for t in re.findall(r"\b(?:TASK-\d+|T\d+)\b", line)})
        planned_files = {f for line in cited for f in re.findall(r"[\w/]+\.py", line)}
        code = sorted(planned_files & diff_files)  # planeado Y realmente cambiado
        findings = sorted(fid for fid, seg in segments.items() if ac in seg)
        rows.append(
            {
                "ac": ac,
                "tasks": tasks or [UNMAPPED],
                "code": code or [UNMAPPED],
                "tests": tests_by_ac.get(ac) or [UNMAPPED],
                "findings": findings or ["-"],
                "decisions": [decisions.get(f, "-") for f in findings] or ["-"],
            }
        )
    return rows


def traceability_gaps(rows: list[dict]) -> int:
    return sum(
        1 for r in rows for col in ("tasks", "code", "tests") if r[col] == [UNMAPPED]
    )


def build_traceability(run: dict, run_dir: Path) -> str:
    rows = compute_traceability(run_dir)
    diff_files = [
        f
        for f in _git(["diff", "--name-only", "main...HEAD"]).stdout.split()
        if f.startswith(("campusflow/", "tests/product/"))
    ]
    table = [
        f"| {r['ac']} | {', '.join(r['tasks'])} | {', '.join(r['code'])} | "
        f"{', '.join(r['tests'])} | {', '.join(r['findings'])} | {', '.join(r['decisions'])} |"
        for r in rows
    ]
    gaps = traceability_gaps(rows)
    return (
        "\n".join(
            [
                f"# Traceability — {run['change_id']}",
                "",
                "| Req / AC | Task | Code | Test | Finding | Human decision |",
                "|---|---|---|---|---|---|",
                *(table or ["| (no AC-* in the Change Request) | - | - | - | - | - |"]),
                "",
                f"**Traceability gaps ({UNMAPPED}): {gaps}** — vinculos que la evidencia del "
                "run no sustenta; el Release Manager debe considerarlos antes de decidir."
                if gaps
                else "Traceability gaps: 0",
                "",
                "Files changed in the run (git diff main...HEAD): "
                + (", ".join(diff_files) or "none recorded"),
                f"Gates: {json.dumps(run['human_gates'])}",
                f"Estado final: {run['status']} — el merge final es exclusivamente humano.",
                "",
            ]
        )
        + "\n"
    )


if __name__ == "__main__":
    import sys

    mode = "adk" if "--adk" in sys.argv else "fake"
    if mode == "adk":  # las credenciales viven SOLO en .env (gitignoreado)
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    ex: AgentExecutor = ADKExecutor() if mode == "adk" else FakeExecutor()
    try:
        st = advance(sys.argv[1], ex)
        print(f"status: {st['status']}")
    except (AwaitingHuman, Blocked) as exc:
        print(f"\nSTOP: {exc}")
        sys.exit(3)
    except TransientModelError as exc:
        print(f"\nREASUMIBLE: {exc}")
        sys.exit(4)
