"""Orchestrator de la fábrica: convierte la pipeline declarativa del starter en un
motor con human gates BLOQUEANTES, evidencia numerada y trazabilidad.

Fases (cada `advance()` ejecuta UNA fase y persiste factory-state.json):
    intake -> gate:requirements -> plan -> gate:plan -> implement+test-analysis
    -> verification -> review+security -> gate:findings -> (fix -> re-verification
    -> re-review)* -> evidence -> gate:release -> READY FOR HUMAN MERGE

Reglas duras (AGENTS.md del curso):
    - Sin veredicto humano registrado, advance() se detiene: silence is never approval.
    - verification FAIL jamas llega a review (R4 del curso).
    - MAX_FIX_ROUNDS acotado; al superarlo el run queda ESCALATED a humano.
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
    "429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "high demand", "rate limit",
)


def _is_transient(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _TRANSIENT_MARKERS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
            if self.ambiguous_cr or re.search(r"intentionally ambiguous|ambigu", cr_text, re.I):
                return {
                    "recommendation": "HUMAN_CLARIFICATION_REQUIRED",
                    "report": f"# Intake {cid}\n\nSTOP: el CR no define politica; "
                    "requiere decision del Product Owner.\n",
                }
            return {
                "recommendation": "PROCEED",
                "report": f"# Intake {cid}\n\nPROCEED: ACs identificados, "
                "sin ambiguedad bloqueante.\n",
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
                "Verificacion: pytest + ruff.\n"
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

        agent = getattr(A, self.AGENT_FOR_PHASE[phase])
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
        recommendation = (
            "HUMAN_CLARIFICATION_REQUIRED"
            if re.search(r"HUMAN_CLARIFICATION_REQUIRED|\bSTOP\b", report)
            else "PROCEED"
        )
        return {
            "report": report or f"# {phase} {context['change_id']} (respuesta vacía)",
            "recommendation": recommendation,
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
                return fn()
            except Exception as exc:
                if not _is_transient(exc):
                    raise
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
            "intake_report",
            "implementation_plan",
            "git_diff",
            "verification_summary",
            "accepted_blocking_findings",
        ):
            if context.get(key):
                bits.append(f"{key.upper()}:\n{str(context[key])[:12000]}")
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


def _gate_decision(run_dir: Path, gate: str) -> dict | None:
    path = run_dir / f"human-{gate}-decision.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _apply_gate_decision(run_dir: Path, change_id: str, gate: str, decision: dict) -> None:
    """REJECTED/DEFERRED en gates 1 escalan; no hay continuacion automatica."""
    if decision["decision"] in {"APPROVED", "MODIFIED"}:
        return
    _update_state(run_dir, status="ESCALATED", stop_reason=f"gate {gate}: {decision['decision']}")
    raise AwaitingHuman(change_id, gate, f"decision {decision['decision']}: intervenga un humano")


def _archive(run_dir: Path, name: str, round_no: int) -> None:
    src = run_dir / name
    if src.is_file():
        src.rename(run_dir / name.replace(".json", f"-round{round_no}.json"))


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
    if status in TERMINAL:
        return state
    cr_text = _read(run_dir / "00-change-request.md")

    # ---- 1 INTAKE
    if status == "INTAKE_PENDING":
        out = executor.run_agent("intake", {"change_id": change_id, "cr_text": cr_text})
        (run_dir / "01-intake-report.md").write_text(out["report"], encoding="utf-8")
        if out.get("recommendation") != "PROCEED":
            _update_state(
                run_dir,
                status="ESCALATED",
                stop_reason="intake: requiere clarificacion del Product Owner",
            )
            raise AwaitingHuman(
                change_id,
                "requirements",
                "intake detecto ambiguedad; resuelva con approve_gate requirements",
            )
        decision = _gate_decision(run_dir, "requirements")
        if decision is None:
            _update_state(run_dir, status="PLAN_PENDING")
            raise AwaitingHuman(
                change_id, "requirements", "el PO debe validar interpretación/alcance"
            )
        _apply_gate_decision(run_dir, change_id, "requirements", decision)
        state = _update_state(
            run_dir,
            status="PLAN_PENDING",
            human_gates={**state["human_gates"], "requirements": decision["decision"]},
        )
        return advance(change_id, executor)

    # ---- 2 PLANNER + gate plan (el Coder bloqueado hasta APPROVED)
    if status == "PLAN_PENDING":
        out = executor.run_agent(
            "plan",
            {
                "change_id": change_id,
                "cr_text": cr_text,
                "intake_report": _read(run_dir / "01-intake-report.md"),
            },
        )
        (run_dir / "03-implementation-plan.md").write_text(out["report"], encoding="utf-8")
        if out.get("recommendation") != "PROCEED":
            _update_state(run_dir, status="ESCALATED", stop_reason="planner: ambiguedad residual")
            raise AwaitingHuman(change_id, "plan", "planner pidio aclaracion")
        decision = _gate_decision(run_dir, "plan")
        if decision is None:
            raise AwaitingHuman(
                change_id, "plan", "el Coder permanece BLOQUEADO hasta plan APPROVED"
            )
        _apply_gate_decision(run_dir, change_id, "plan", decision)
        branch = f"factory/{change_id}"
        if _git(["rev-parse", "--verify", branch]).returncode != 0:
            _git(["checkout", "-b", branch])
        _update_state(
            run_dir,
            status="IMPLEMENT_PENDING",
            branch=branch,
            human_gates={**state["human_gates"], "plan": decision["decision"]},
        )
        return advance(change_id, executor)

    # ---- 3 CODER + TEST ANALYST
    if status == "IMPLEMENT_PENDING":
        fix_reason = state.get("fix_reason", "")
        ctx = {
            "change_id": change_id,
            "implementation_plan": _read(run_dir / "03-implementation-plan.md"),
            "accepted_blocking_findings": fix_reason,
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
            out2 = executor.run_agent("test_analysis", ctx)
            (run_dir / "06-test-analysis.md").write_text(out2["report"], encoding="utf-8")
        _update_state(
            run_dir,
            status="VERIFY_PENDING",
            fix_reason="",
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
            if round_no >= MAX_FIX_ROUNDS:
                _update_state(
                    run_dir,
                    status="FIX_ROUNDS_EXCEEDED",
                    verification=ver,
                    stop_reason=f"verification FAIL tras {round_no} rondas",
                )
                raise Blocked(f"{change_id}: verification FAIL persistente -> intervencion humana")
            _update_state(
                run_dir,
                status="IMPLEMENT_PENDING",
                iterations=round_no + 1,
                verification=ver,
                fix_reason="verification FAIL",
            )
            return advance(change_id, executor)
        _update_state(run_dir, status="REVIEW_PENDING", verification=ver)
        return advance(change_id, executor)

    # ---- 5 REVIEWER + SECURITY independientes + gate findings
    if status == "REVIEW_PENDING":
        diff = _git(["diff", "main...HEAD"]).stdout
        review_ctx = {
            "change_id": change_id,
            "cr_text": cr_text,
            "implementation_plan": _read(run_dir / "03-implementation-plan.md"),
            "git_diff": diff,
            "verification_summary": _read(run_dir / "07-verification.txt"),
        }
        review = executor.run_agent("review", review_ctx)  # sesion propia
        (run_dir / "08-review-report.md").write_text(review["report"], encoding="utf-8")
        sec = executor.run_agent(
            "security", {"change_id": change_id, "cr_text": cr_text, "git_diff": diff}
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
        counts = count_findings(review["report"] + "\n" + sec["report"])
        review_status = "PASS" if counts["must_fix"] == 0 else "CHANGES_REQUESTED"
        security_status = "BLOCKED" if (counts["high"] > 0 or scans["blocked"]) else "PASS"
        state = _update_state(
            run_dir,
            review={"status": review_status, **{k: counts[k] for k in ("must_fix", "should_fix")}},
            security={
                "status": security_status,
                "high": counts["high"],
                "medium": counts["medium"],
            },
        )
        decision = _gate_decision(run_dir, "review")
        if decision is None:
            raise AwaitingHuman(
                change_id, "review", "clasifique cada finding (ACCEPTED/REJECTED/MODIFIED/DEFERRED)"
            )
        blocking = accepted_blocking(run_dir)
        rounds = state.get("iterations", 0)
        if blocking:
            _archive(run_dir, "human-review-decision.json", rounds)
            _archive(run_dir, "human-findings-decisions.json", rounds)
            _update_state(
                run_dir,
                status="IMPLEMENT_PENDING",
                iterations=rounds + 1,
                human_gates={**state["human_gates"], "review": decision["decision"]},
                fix_reason="findings bloqueantes aceptados: " + ", ".join(blocking),
            )
            return advance(change_id, executor)
        _update_state(
            run_dir,
            status="EVIDENCE_PENDING",
            human_gates={**state["human_gates"], "review": decision["decision"]},
        )
        return advance(change_id, executor)

    # ---- 6 EVIDENCIA + gate release
    if status == "EVIDENCE_PENDING":
        state = json.loads(_state_path(run_dir).read_text(encoding="utf-8"))
        run_json = build_factory_run(change_id, state, run_dir)
        (run_dir / "factory-run.json").write_text(json.dumps(run_json, indent=2), encoding="utf-8")
        (run_dir / "14-evidence-summary.md").write_text(
            build_evidence_summary(run_json, run_dir), encoding="utf-8"
        )
        (run_dir / "traceability.md").write_text(
            build_traceability(run_json, run_dir), encoding="utf-8"
        )
        decision = _gate_decision(run_dir, "release")
        if decision is None:
            _update_state(run_dir, status="EVIDENCE_PENDING", evidence="CONSOLIDATED")
            raise AwaitingHuman(
                change_id, "release", "Release Manager decide con evidencia consolidada"
            )
        verdict = decision["decision"]
        final = {"APPROVED": "READY FOR HUMAN MERGE", "REJECTED": "NOT READY"}.get(
            verdict, "ESCALATED"
        )
        state = _update_state(
            run_dir,
            status=final,
            evidence="CONSOLIDATED",
            human_gates={**state["human_gates"], "release": verdict},
            release_decision=decision,
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


def accepted_blocking(run_dir: Path) -> list[str]:
    path = run_dir / "human-findings-decisions.json"
    if not path.is_file():
        return []  # sin decision explicita, ningun finding se auto-aplica (Gate 2 real)
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        d["finding_id"]
        for d in data.get("decisions", [])
        if d.get("decision") in {"ACCEPTED", "MODIFIED"}
    ]


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
                f"- Artefactos ({len(present)}): {', '.join(present)}",
                "",
                "> Generado por factory_app/orchestrator desde evidencia real del run.",
                "",
            ]
        )
        + "\n"
    )


def build_traceability(run: dict, run_dir: Path) -> str:
    acs = sorted(set(re.findall(r"AC-CR\d+\.\d+", _read(run_dir / "00-change-request.md"))))
    coder = _read(run_dir / "05-coder-report.md") + _read(run_dir / "11-fix-report.md")
    code_files = re.findall(r"(campusflow/[\w/\.]+\.py)", coder) or ["campusflow/service.py"]
    findings = sorted(
        set(
            re.findall(
                r"(RF-\d+|SEC-\d+)",
                _read(run_dir / "08-review-report.md") + _read(run_dir / "09-security-report.md"),
            )
        )
    )
    dec_path = run_dir / "human-findings-decisions.json"
    decisions = (
        {
            d["finding_id"]: d["decision"]
            for d in json.loads(dec_path.read_text()).get("decisions", [])
        }
        if dec_path.is_file()
        else {}
    )
    rows = []
    for i, ac in enumerate(acs):
        f = findings[i] if i < len(findings) else "-"
        rows.append(
            f"| {ac} | T1 | {code_files[0]} | tests/product/ | {f} | {decisions.get(f, '-')} |"
        )
    return (
        "\n".join(
            [
                f"# Traceability — {run['change_id']}",
                "",
                "| Req / AC | Task | Code | Test | Finding | Human decision |",
                "|---|---|---|---|---|---|",
                *rows,
                "",
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
