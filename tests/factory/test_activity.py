"""Actividad en vivo (activity.jsonl): quien trabaja en que, con datos reales del motor.

Solo FakeExecutor / repos temporales: no requiere API key ni toca runs/ reales.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from factory_app import activity, audit, services
from factory_app.context import project
from factory_app.orchestrator import Blocked, FakeExecutor
from tests.factory.test_orchestrator import (
    CR_CLEAN,
    approve,
    drive,
    sandbox,  # noqa: F401  (fixture)
)


def events(run, kind=None):
    items = activity.read(run)["items"]
    return [e for e in items if kind is None or e["kind"] == kind]


def test_full_run_records_who_did_what(sandbox):  # noqa: F811
    run = sandbox()
    assert drive(run, FakeExecutor())["status"] == "READY FOR HUMAN MERGE"
    starts = [(e["phase"], e["role"]) for e in events(run, "phase_start")]
    assert starts == [
        ("intake", "intake"),
        ("plan", "planner"),
        ("implement", "coder"),
        ("test_analysis", "test_analyst"),
        ("verify", "verify"),
        ("review", "reviewer"),
        ("security", "security"),
        ("evidence", "evidence"),
    ]
    ends = events(run, "phase_end")
    assert len(ends) == len(starts)
    assert all(isinstance(e.get("duration_ms"), int) for e in ends)
    assert {e["provider"] for e in events(run, "phase_start") if "provider" in e} == {"fake"}
    gates = [e["gate"] for e in events(run, "gate_open")]
    assert gates == ["requirements", "plan", "review", "release"]
    assert [e["decision"] for e in events(run, "gate_decision")] == ["APPROVED"] * 4
    assert events(run, "gate_open")[0]["roles"] == ["product owner"]
    assert events(run, "done")[-1]["status"] == "READY FOR HUMAN MERGE"
    assert [e["kind"] for e in events(run, None) if e["kind"].startswith("scan_")] == [
        "scan_start", "scan_end"]
    seqs = [e["seq"] for e in events(run)]
    assert seqs == list(range(1, len(seqs) + 1))  # secuencia continua para leer en vivo


def test_fix_rounds_and_escalation_are_visible(sandbox):  # noqa: F811
    run = sandbox()
    with pytest.raises(Blocked):
        drive(run, FakeExecutor(verify_fail=99))
    rounds = events(run, "fix_round")
    cap = project().config.limits.verify_rounds
    assert [(e["fix"], e["n"], e["cap"]) for e in rounds] == [
        ("verify", n, cap) for n in range(1, cap + 1)]
    verify_ends = [e for e in events(run, "phase_end") if e["phase"] == "verify"]
    assert verify_ends and not any(e["ok"] for e in verify_ends)
    assert "verification FAIL" in events(run, "escalated")[-1]["reason"]


def test_reading_after_a_seq_is_incremental(sandbox):  # noqa: F811
    run = sandbox()
    drive(run, FakeExecutor(), stop_at="plan")
    first = activity.read(run)
    assert first["last"] == len(first["items"])
    approve(run, "plan")
    drive(run, FakeExecutor(), stop_at="review")
    more = activity.read(run, after=first["last"])
    assert more["items"][0]["seq"] == first["last"] + 1
    assert all(e["seq"] > first["last"] for e in more["items"])


def test_without_an_active_run_nothing_is_written(tmp_path):
    assert activity.emit("phase_start", phase="intake") is None  # CLI fuera de advance()
    assert not list(tmp_path.iterdir())


def test_verification_commands_are_reported_one_by_one(sandbox, monkeypatch):  # noqa: F811
    from factory_app import tools

    run = sandbox()
    monkeypatch.setattr(tools, "work_root", lambda: run.parents[1])
    with activity.track(run):
        tools.run_baseline_verification()
    labels = [c.label() for c in project().config.verify.commands]
    starts = [e["command"] for e in events(run, "verify_cmd_start")]
    ends = events(run, "verify_cmd_end")
    assert starts == labels and [e["command"] for e in ends] == labels
    assert all("duration_ms" in e and "returncode" in e for e in ends)
    assert not any("stdout" in e for e in ends)  # sin la salida de los comandos


def test_tool_calls_stream_short_targets_only(sandbox):  # noqa: F811
    run = sandbox()
    ctx = SimpleNamespace(state={"change_id": "CR-T", "phase": "implement"},
                          agent_name="CoderAgent")
    tool = SimpleNamespace(name="write_product_file")
    big = "x = 1\n" * 500
    args = {"path": "campusflow/service.py", "content": big}
    audit.record_tool_start(tool, args, ctx)
    audit.record_tool_call(tool, args, ctx, {"ok": True})
    start, end = events(run, "tool_start")[0], events(run, "tool_end")[0]
    assert start["target"] == "campusflow/service.py" and start["agent"] == "CoderAgent"
    assert end["ok"] is True and isinstance(end["duration_ms"], int)
    assert big not in json.dumps(events(run))  # nunca el contenido completo
    # la evidencia de tools sigue igual
    assert (run / audit.AUDIT_FILE).is_file()


def test_live_reports_the_open_phase(sandbox):  # noqa: F811
    run = sandbox()
    activity.emit("phase_start", run, phase="implement", role="coder", provider="anthropic",
                  model="claude-x")
    activity.emit("tool_start", run, phase="implement", tool="read_repo_file", target="a.py")
    live = activity.live(run)
    assert live["role"] == "coder" and live["tool"] == "read_repo_file"
    assert live["target"] == "a.py" and live["model"] == "claude-x"
    activity.emit("phase_end", run, phase="implement", role="coder")
    assert activity.live(run) is None


def test_retry_archives_the_activity_with_its_attempt(sandbox):  # noqa: F811
    run = sandbox()
    p = project()
    p.cr_dir.mkdir(parents=True, exist_ok=True)
    (p.cr_dir / "CR-T-x.md").write_text(CR_CLEAN, encoding="utf-8")
    drive(run, FakeExecutor(), decisions={"plan": "REJECTED"})
    services.retry_run(p, "CR-T", CR_CLEAN, "otra vez", "Ana (Product Owner)")
    assert (run / "intentos" / "1" / activity.ACTIVITY_FILE).is_file()
    assert not (run / activity.ACTIVITY_FILE).exists()
    drive(run, FakeExecutor(), stop_at="requirements")
    assert events(run)[0]["seq"] == 1  # el intento nuevo empieza su propia secuencia
