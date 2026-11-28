"""ADKExecutor contra un modelo falso (BaseLlm local, sin red ni API key): texto final
completo, config de los agentes, instancias por fase, cierre del runner y auditoria de tools."""

from __future__ import annotations

import inspect
import json
from typing import AsyncGenerator

import pytest

pytest.importorskip("google.adk")

from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from factory_app import agents, audit, tools, workflows  # noqa: E402
from factory_app.context import project  # noqa: E402
from factory_app.orchestrator import ADKExecutor, final_text  # noqa: E402
from tests.factory.conftest import activate_project  # noqa: E402


class FakeLlm(BaseLlm):
    """1a llamada: pide la tool list_repo_files; 2a: responde en 3 partes (thought+texto)."""

    model: str = "fake"
    requests: list = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        answered = any(
            p.function_response for c in llm_request.contents for p in (c.parts or [])
        )
        if not answered:
            call = types.FunctionCall(name="list_repo_files", args={"prefix": "docs"})
            parts = [types.Part(function_call=call)]
        else:
            parts = [
                types.Part(text="razonamiento interno", thought=True),
                types.Part(text="# Plan\nPaso 1\n"),
                types.Part(text="RECOMMENDATION: PROCEED"),
            ]
        yield LlmResponse(content=types.Content(role="model", parts=parts))


@pytest.fixture()
def fake_model(monkeypatch, tmp_path):
    llm = FakeLlm(requests=[])
    real = agents._agent

    def with_fake(*args, **kwargs):
        agent = real(*args, **kwargs)
        agent.model = llm
        return agent

    monkeypatch.setattr(agents, "_agent", with_fake)  # las fabricas lo resuelven al llamar
    activate_project(tmp_path, monkeypatch)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
    closed = {"n": 0}
    real_close = InMemoryRunner.close

    async def counting_close(self):
        closed["n"] += 1
        await real_close(self)

    monkeypatch.setattr(InMemoryRunner, "close", counting_close)
    return llm, tmp_path, closed


def test_final_text_joins_all_text_parts_without_thoughts():
    parts = [
        types.Part(text="oculto", thought=True),
        types.Part(text="# Plan\n"),
        types.Part(function_call=types.FunctionCall(name="x", args={})),
        types.Part(text="RECOMMENDATION: PROCEED"),
    ]
    assert final_text(parts) == "# Plan\nRECOMMENDATION: PROCEED"


def test_executor_keeps_the_whole_report_and_closes_the_runner(fake_model):
    llm, _tmp, closed = fake_model
    out = ADKExecutor().run_agent("plan", {"change_id": "CR-001", "cr_text": "CR body"})
    assert out["report"] == "# Plan\nPaso 1\nRECOMMENDATION: PROCEED"
    assert "razonamiento interno" not in out["report"]
    assert out["recommendation"] == "PROCEED"
    assert closed["n"] == 1
    assert llm.requests[0].config.temperature == project().config.agents.temperature


def test_tool_calls_are_audited_per_run(fake_model):
    _llm, tmp, _closed = fake_model
    ADKExecutor().run_agent("plan", {"change_id": "CR-001", "cr_text": "CR body"})
    lines = (tmp / "runs" / "CR-001" / audit.AUDIT_FILE).read_text(encoding="utf-8")
    entry = json.loads(lines.strip().splitlines()[-1])
    assert entry["tool"] == "list_repo_files" and entry["args"] == {"prefix": "docs"}
    assert entry["phase"] == "plan" and entry["agent"] == "PlannerAgent"
    assert entry["ok"] is True


def test_audit_truncates_long_args_and_ignores_sessions_without_run(tmp_path, monkeypatch):
    activate_project(tmp_path, monkeypatch)

    class Ctx:
        agent_name = "CoderAgent"

        def __init__(self, state):
            self.state = state

    class Tool:
        name = "write_product_file"

    args = {"relative_path": "campusflow/a.py", "content": "x" * 1000}
    audit.record_tool_call(Tool(), args, Ctx({"change_id": "CR-002", "phase": "fix"}), {"ok": True})
    audit.record_tool_call(Tool(), args, Ctx({}), {"ok": True})  # sin run: no escribe
    audit.record_tool_call(Tool(), args, Ctx({"change_id": "../x"}), {"ok": True})
    files = list((tmp_path / "runs").rglob(audit.AUDIT_FILE))
    assert [f.parent.name for f in files] == ["CR-002"]
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(entry["args"]["content"]) < 400 and "TRUNCATED 700" in entry["args"]["content"]


def test_each_phase_gets_a_fresh_parentless_agent():
    a, b = agents.make_coder_agent(), agents.make_coder_agent()
    assert a is not b and a.parent_agent is None
    pipeline_agents = workflows.planning_pipeline.sub_agents
    assert agents.intake_agent not in pipeline_agents
    assert agents.intake_agent.parent_agent is None


def test_agents_use_low_temperature_and_no_internal_retries():
    for factory in agents.AGENT_FACTORIES.values():
        agent = factory()
        assert agent.generate_content_config.temperature == project().config.agents.temperature
        assert agent.model.retry_options.attempts == 1  # solo reintenta el orquestador
        assert agent.after_tool_callback is audit.record_tool_call


@pytest.mark.parametrize(
    "fn",
    [
        tools.list_repo_files, tools.read_repo_file, tools.run_baseline_verification,
        tools.write_run_evidence, tools.write_product_file, tools.write_test_file,
        tools.inspect_git_diff, tools.run_security_scans_tool,
    ],
)
def test_agent_tools_have_no_defaults_and_document_args(fn):
    params = inspect.signature(fn).parameters.values()
    assert all(p.default is inspect.Parameter.empty for p in params), fn.__name__
    doc = inspect.getdoc(fn) or ""
    assert "Returns:" in doc
    if any(True for _ in inspect.signature(fn).parameters):
        assert "Args:" in doc


def test_run_records_provider_model_and_effective_temperature(fake_model):
    _llm, tmp, _closed = fake_model
    run_dir = tmp / "runs" / "CR-001"
    run_dir.mkdir(parents=True)
    (run_dir / "factory-state.json").write_text(json.dumps({"status": "PLANNING"}))
    ADKExecutor().run_agent("plan", {"change_id": "CR-001", "cr_text": "CR body"})
    state = json.loads((run_dir / "factory-state.json").read_text(encoding="utf-8"))
    agents_cfg = project().config.agents
    assert state["models"]["planner"] == {
        "provider": "gemini", "model": agents_cfg.model_for("planner"),
        "temperature_requested": agents_cfg.temperature,
        "temperature_effective": agents_cfg.temperature,
    }
    assert state["status"] == "PLANNING"  # registrar el modelo no es una transicion


def test_temperature_is_omitted_when_the_probe_says_the_model_rejects_it(fake_model,
                                                                         monkeypatch):
    llm, _tmp, _closed = fake_model
    monkeypatch.setattr(agents, "temperature_supported", lambda p, m: False)
    assert agents.make_planner_agent().generate_content_config.temperature is None
    assert agents.model_settings("planner")["temperature_effective"] is None
    ADKExecutor().run_agent("plan", {"change_id": "CR-001", "cr_text": "CR body"})
    assert llm.requests[-1].config.temperature is None
