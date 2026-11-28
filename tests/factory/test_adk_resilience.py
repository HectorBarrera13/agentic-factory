"""Modo --adk sin API: parseo de la recomendacion, placeholders de instrucciones,
reintentos transitorios, truncado visible y contexto del Coder Fix / Test Analyst."""

from __future__ import annotations

import re

import pytest

from factory_app import orchestrator as ORCH
from factory_app.orchestrator import ADKExecutor, AwaitingHuman, FakeExecutor
from tests.factory.test_orchestrator import (
    approve,
    decide,
    drive,
    sandbox,  # noqa: F401  (fixture)
    state,
)


# ---------------------------------------------------------------- RECOMMENDATION
@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ("PROCEED, no HUMAN_CLARIFICATION_REQUIRED\nRECOMMENDATION: PROCEED", "PROCEED"),
        ("Stop conditions: STOP if unclear.\nRECOMMENDATION: PROCEED\n", "PROCEED"),
        ("**RECOMMENDATION:** HUMAN_CLARIFICATION_REQUIRED", "HUMAN_CLARIFICATION_REQUIRED"),
        ("RECOMMENDATION: PROCEED\n...\nRECOMMENDATION: HUMAN_CLARIFICATION_REQUIRED",
         "HUMAN_CLARIFICATION_REQUIRED"),  # la ultima manda
        ("STOP: falta politica, HUMAN_CLARIFICATION_REQUIRED", "MISSING"),
        ("   \n", "EMPTY"),
    ],
)
def test_recommendation_is_read_only_from_its_line(report, expected):
    assert ORCH.parse_recommendation(report) == expected


class Answers(FakeExecutor):
    def __init__(self, **by_phase):
        super().__init__()
        self.by_phase = by_phase

    def run_agent(self, phase, context):
        if phase in self.by_phase:
            return {"report": "# x\n", "recommendation": self.by_phase.pop(phase)}
        return super().run_agent(phase, context)


@pytest.mark.parametrize("reco", ["MISSING", "EMPTY"])
def test_intake_without_valid_recommendation_escalates(sandbox, reco):  # noqa: F811
    run = sandbox()
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", Answers(intake=reco))
    st = state(run)
    assert st["status"] == "ESCALATED" and "sin RECOMMENDATION valida" in st["stop_reason"]
    assert st["stop_reason"].startswith("intake:")  # reanudable con decision del PO
    assert not (run / "03-implementation-plan.md").exists()


def test_planner_without_valid_recommendation_escalates(sandbox):  # noqa: F811
    run = sandbox()
    drive(run, Answers(plan="MISSING"), stop_at="plan")
    st = state(run)
    assert st["status"] == "ESCALATED" and st["stop_reason"].startswith("planner:")
    assert not (run / "05-coder-report.md").exists()


# ---------------------------------------------------------------- reportes sin contenido
PLAN = (
    "| Task | AC | files | tests |\n|---|---|---|---|\n"
    "| T1 sala | AC-01 | js/sync.js | tests/sync.test.mjs |\n"
)


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ("RECOMMENDATION: PROCEED", "NO_CONTENT"),
        ("# Plan\n\nRECOMMENDATION: PROCEED\n", "NO_CONTENT"),
        (PLAN + "\nRECOMMENDATION: PROCEED", "PROCEED"),
        # pedir aclaracion no necesita un reporte largo
        ("RECOMMENDATION: HUMAN_CLARIFICATION_REQUIRED", "HUMAN_CLARIFICATION_REQUIRED"),
        ("", "EMPTY"),
    ],
)
def test_a_proceed_without_content_is_not_a_report(report, expected):
    assert ORCH.judge_report(report) == expected


def test_the_report_is_rebuilt_from_the_whole_phase_when_the_final_message_is_bare():
    final = "RECOMMENDATION: PROCEED"
    assert ORCH.assemble_report(final, [PLAN, final]) == PLAN + "\n\n" + final
    full = PLAN + "\nRECOMMENDATION: PROCEED"
    assert ORCH.assemble_report(full, ["leyendo...", full]) == full  # el final ya basta
    assert ORCH.assemble_report(final, [final]) == final  # no hay nada mas que recuperar


@pytest.mark.parametrize(("phase", "prefix", "gate"), [("intake", "intake:", "requirements"),
                                                        ("plan", "planner:", "plan")])
def test_an_empty_report_escalates_and_a_clarification_regenerates_it(sandbox, phase, prefix, gate):  # noqa: F811
    run = sandbox()
    drive(run, Answers(**{phase: "NO_CONTENT"}), stop_at=gate)
    st = state(run)
    assert st["status"] == "ESCALATED" and st["stop_reason"].startswith(prefix)
    assert "solo la linea RECOMMENDATION" in st["stop_reason"]
    # el humano aclara: el artefacto se regenera y vuelve a su gate sin gastar un intento
    approve(run, gate, "MODIFIED")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", FakeExecutor())
    assert exc.value.gate == gate
    st = state(run)
    assert st.get("attempt", 1) == 1 and len(st["po_clarifications"]) == 1



def test_intake_and_planner_prompts_demand_the_recommendation_line():
    from factory_app import prompts

    for text in (prompts.INTAKE_INSTRUCTION, prompts.PLANNER_INSTRUCTION):
        assert "RECOMMENDATION: PROCEED" in text
        assert "RECOMMENDATION: HUMAN_CLARIFICATION_REQUIRED" in text


# ---------------------------------------------------------------- placeholders ADK
def test_instruction_placeholders_are_optional_in_fresh_sessions():
    """Cada fase corre en una sesion ADK NUEVA (estado vacio): un `{var}` obligatorio
    lanza KeyError 'Context variable not found'. Todos deben ser `{var?}`."""
    pytest.importorskip("google.adk")
    from factory_app import agents

    for name in dir(agents):
        agent = getattr(agents, name)
        instruction = getattr(agent, "instruction", None)
        if not isinstance(instruction, str):
            continue
        for var in re.findall(r"\{([A-Za-z_][\w.]*\??)\}", instruction):
            assert var.endswith("?"), f"{name}: placeholder obligatorio {{{var}}}"


# ---------------------------------------------------------------- reintentos
@pytest.mark.parametrize(
    "msg", ["RESOURCE_EXHAUSTED: quota", "UNAVAILABLE", "429 Too Many", "model under high demand"]
)
def test_transient_errors_are_detected(msg):
    assert ORCH._is_transient(Exception(msg))


def test_non_transient_error_is_not_retried():
    assert not ORCH._is_transient(ValueError("Context variable not found"))


def test_successful_retries_are_recorded_in_state(sandbox, monkeypatch):  # noqa: F811
    import time

    run = sandbox()
    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("503 UNAVAILABLE")
        return "ok"

    assert ADKExecutor(backoff_base=0)._call_with_backoff(flaky, "intake", "CR-T") == "ok"
    st = state(run)
    assert st["llm_retries"] == 2 and "EXHAUSTED" not in st["last_llm_note"]


def test_exhausted_retries_degrade_the_stored_probe(sandbox, monkeypatch):  # noqa: F811
    import time

    from factory_app import probe

    run = sandbox()
    monkeypatch.setattr(time, "sleep", lambda s: None)
    probe._store("gemini", "g-z", {"status": "OK", "at": time.time()})

    def always_429():
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    ex = ADKExecutor(backoff_base=0, max_retries=1)
    with pytest.raises(ORCH.TransientModelError):
        ex._call_with_backoff(always_429, "intake", "CR-T", model=("gemini", "g-z"))
    assert probe.result("gemini", "g-z")["status"] == "QUOTA"
    assert "EXHAUSTED" in state(run)["last_llm_note"]
    # al reanudar, la fase sale bien a la primera: la nota vieja ya no describe el run
    assert ex._call_with_backoff(lambda: "ok", "intake", "CR-T") == "ok"
    assert "EXHAUSTED" not in state(run)["last_llm_note"]


def test_long_prompt_fields_are_visibly_truncated():
    p = ADKExecutor._prompt("review", {"change_id": "CR-T", "git_diff": "x" * 20_000})
    assert "[TRUNCATED 8000 chars" in p


# ---------------------------------------------------------------- contexto por fase
class Capture(FakeExecutor):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.ctx: dict[str, dict] = {}

    def run_agent(self, phase, context):
        self.ctx[phase] = context
        return super().run_agent(phase, context)


def test_fix_receives_the_accepted_findings_text(sandbox):  # noqa: F811
    run = sandbox()
    ex = Capture(review_report="# Review\n\nRF-01 | MUST FIX | race condition in register\n")
    drive(run, ex, stop_at="review")
    decide(run, RF_01="ACCEPTED")
    approve(run, "review")
    drive(run, ex, stop_at="review")
    fix = ex.ctx["fix"]["accepted_blocking_findings"]
    assert "RF-01" in fix and "race condition in register" in fix


def _decide_with(run, **decisions):
    """Clasificacion con campos nuevos (motivo, "corregir en este ciclo") registrada AHORA."""
    import json
    from datetime import datetime, timezone

    (run / "human-findings-decisions.json").write_text(json.dumps({
        "decisions": [{"finding_id": fid.replace("_", "-"), **d} for fid, d in decisions.items()],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")


def test_a_should_fix_marked_by_the_reviewer_triggers_the_fix_with_its_reason(sandbox):  # noqa: F811
    run = sandbox()
    ex = Capture(review_report="# Review\n\nRF-01 | SHOULD FIX | AC-03 | js/app.js | sin usar\n")
    drive(run, ex, stop_at="review")
    _decide_with(run, RF_01={"decision": "ACCEPTED", "blocking": True,
                             "reason": "index.html debe cargar js/sync.js"})
    approve(run, "review")
    drive(run, ex, stop_at="review")
    fix = ex.ctx["fix"]["accepted_blocking_findings"]
    assert "RF-01" in fix and "Nota del Human Reviewer: index.html debe cargar js/sync.js" in fix
    table = (run / "10-human-review-decisions.md").read_text(encoding="utf-8")
    row = "| RF-01 | SHOULD FIX | ACCEPTED | yes (human) | index.html debe cargar js/sync.js"
    assert row in table


def test_the_blocking_mark_needs_an_accepting_verdict(sandbox):  # noqa: F811
    run = sandbox()
    ex = Capture(review_report="# Review\n\nRF-01 | SHOULD FIX | naming\n")
    drive(run, ex, stop_at="review")
    _decide_with(run, RF_01={"decision": "DEFERRED", "blocking": True})
    exc = drive(run, ex, stop_at="release")
    assert exc.gate == "release" and "fix" not in ex.ctx  # un DEFERRED nunca cambia codigo


def test_fix_after_verification_fail_receives_the_failure(sandbox):  # noqa: F811
    run = sandbox()
    ex = Capture(verify_fail=1)
    drive(run, ex, stop_at="review")
    assert "2 failed" in ex.ctx["fix"]["verification_summary"]


def test_coder_and_test_analyst_receive_the_change_request(sandbox):  # noqa: F811
    run = sandbox()
    ex = Capture()
    drive(run, ex, stop_at="review")
    assert "AC-CRT.1" in ex.ctx["implement"]["cr_text"]
    assert "AC-CRT.1" in ex.ctx["test_analysis"]["cr_text"]
    assert "git_diff" in ex.ctx["test_analysis"]
