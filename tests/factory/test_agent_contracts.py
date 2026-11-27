"""§8 del curso: cada agente declara su contrato y el contrato coincide con la realidad
(las tools que el agente REALMENTE tiene), no solo con el prompt."""

from __future__ import annotations

import pytest

pytest.importorskip("google.adk")

from factory_app import agents  # noqa: E402
from factory_app.contracts import (  # noqa: E402
    CONTRACTS,
    GLOBAL_DENY,
    MANDATORY_AGENTS,
    REQUIRED_FIELDS,
)

AGENT_OBJECTS = {
    "intake": agents.intake_agent,
    "planner": agents.planner_agent,
    "coder": agents.coder_agent,
    "test_analyst": agents.test_analyst_agent,
    "reviewer": agents.reviewer_agent,
    "security": agents.security_agent,
    "evidence": agents.evidence_agent,
}
WRITE_TOOLS = {"write_product_file", "write_test_file", "write_run_evidence"}


def _tool_names(agent) -> set[str]:
    return {getattr(t, "__name__", getattr(t, "name", "?")) for t in agent.tools}


def test_six_mandatory_agents_have_complete_contracts():
    for key in MANDATORY_AGENTS:
        contract = CONTRACTS[key]
        for field in REQUIRED_FIELDS:
            assert contract.get(field), f"{key}: falta {field}"


@pytest.mark.parametrize("key", sorted(CONTRACTS))
def test_contract_allow_matches_real_agent_tools(key):
    assert set(CONTRACTS[key]["allow"]) == _tool_names(AGENT_OBJECTS[key]), key


@pytest.mark.parametrize("key", sorted(CONTRACTS))
def test_every_agent_denies_merge_requirements_and_weakening(key):
    assert set(GLOBAL_DENY) <= set(CONTRACTS[key]["deny"]), key


@pytest.mark.parametrize("key", ["intake", "planner", "reviewer", "security"])
def test_read_only_agents_have_no_write_tools(key):
    assert not (_tool_names(AGENT_OBJECTS[key]) & WRITE_TOOLS), key


def test_denied_tools_are_never_granted():
    for key, contract in CONTRACTS.items():
        assert not (set(contract["deny"]) & _tool_names(AGENT_OBJECTS[key])), key


def test_test_analyst_cannot_write_application_code():
    tools = _tool_names(agents.test_analyst_agent)
    assert "write_test_file" in tools and "write_product_file" not in tools
