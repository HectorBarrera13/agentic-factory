"""Wiring ADK: la fabrica expone los 7 agentes correctos con herramientas correctas.

No requiere API key: solo construye objetos. La corrida real de agentes es T03+ (con key).
"""

import pytest

pytest.importorskip("google.adk")

from factory_app import agents  # noqa: E402
from factory_app.orchestrator import ADKExecutor  # noqa: E402


def agent_names():
    return [
        agents.intake_agent,
        agents.planner_agent,
        agents.coder_agent,
        agents.test_analyst_agent,
        agents.reviewer_agent,
        agents.security_agent,
        agents.evidence_agent,
    ]


def test_seven_agents_exist():
    assert len(agent_names()) == 7
    for a in agent_names():
        assert a.name and a.instruction


def test_permissions_by_role():
    def tool_names(a):
        return [getattr(t, "__name__", getattr(t, "name", "?")) for t in a.tools]

    names = {a.name: tool_names(a) for a in agent_names()}
    for readonly in ("IntakeAgent", "PlannerAgent", "ReviewerAgent"):
        assert "write_product_file" not in names[readonly]
        assert "write_run_evidence" not in names[readonly]
    assert "write_product_file" in names["CoderAgent"]
    assert "write_run_evidence" not in names["CoderAgent"]  # el coder no escribe evidencia
    assert "write_product_file" not in names["EvidenceAgent"]
    assert "run_security_scans_tool" in names["SecurityAgent"]
    assert "write_product_file" not in names["SecurityAgent"]


def test_phase_map_complete_and_named():
    for phase, attr in ADKExecutor.AGENT_FOR_PHASE.items():
        assert hasattr(agents, attr), phase


def test_reviewer_prompt_carries_artifacts_not_coder_session():
    p = ADKExecutor._prompt(
        "review",
        {
            "change_id": "CR-001",
            "cr_text": "CR body",
            "git_diff": "diff --git",
            "verification_summary": "PASS 12",
            "implementation_plan": "plan",
        },
    )
    for frag in ("CR_TEXT", "GIT_DIFF", "VERIFICATION_SUMMARY", "IMPLEMENTATION_PLAN"):
        assert frag in p
    assert "CODER_REASONING" not in p  # nunca se propaga sesion del coder
