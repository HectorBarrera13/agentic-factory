"""Scripts humanos (decide_findings, approve_gate, start_run, export_evidence) y tools."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from factory_app import orchestrator as ORCH
from factory_app import tools
from factory_app.orchestrator import FakeExecutor
from tests.factory.conftest import activate_project
from tests.factory.test_orchestrator import (
    drive,
    sandbox,  # noqa: F401  (fixture)
    state,
)

ROOT = Path(__file__).resolve().parents[2]
REVIEWER = "Ana (Human Reviewer)"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def _cli(script: str, *args: str):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        capture_output=True, text=True, check=False,
    )


@pytest.fixture()
def review_run():
    """Run real (runs/ gitignoreado) detenido en el gate review con 3 findings."""
    name = "CR-FINDINGS-TEST"
    run = ROOT / "runs" / name
    run.mkdir(parents=True)
    (run / "factory-state.json").write_text(
        json.dumps({"status": "REVIEW_PENDING", "awaiting_gate": "review",
                    "human_gates": {"review": "PENDING"}})
    )
    (run / "08-review-report.md").write_text("RF-01 | MUST FIX | a\nRF-02 | SHOULD FIX | b\n")
    (run / "09-security-report.md").write_text("SEC-SCAN-01 | HIGH | - | x.py:1 | r (semgrep)\n")
    yield name, run
    for f in run.iterdir():
        f.unlink()
    run.rmdir()


def _decisions(run: Path) -> dict:
    data = json.loads((run / "human-findings-decisions.json").read_text())
    return {d["finding_id"]: d["decision"] for d in data["decisions"]}


def test_decide_findings_merges_calls(review_run):
    name, run = review_run
    assert _cli("decide_findings.py", name, "--by", REVIEWER, "RF-01=ACCEPTED").returncode == 0
    assert _cli("decide_findings.py", name, "--by", REVIEWER, "RF-02=REJECTED").returncode == 0
    assert _decisions(run) == {"RF-01": "ACCEPTED", "RF-02": "REJECTED"}
    _cli("decide_findings.py", name, "--by", REVIEWER, "RF-01=DEFERRED")
    assert _decisions(run)["RF-01"] == "DEFERRED"  # la ultima decision gana


def test_review_gate_requires_every_finding_classified(review_run):
    name, run = review_run
    _cli("decide_findings.py", name, "--by", REVIEWER, "RF-01=ACCEPTED", "RF-02=REJECTED")
    r = _cli("approve_gate.py", name, "review", "APPROVED", "ok", "--by", REVIEWER)
    assert r.returncode != 0 and "SEC-SCAN-01" in (r.stdout + r.stderr)
    assert not (run / "human-review-decision.json").exists()
    _cli("decide_findings.py", name, "--by", REVIEWER, "SEC-SCAN-01=DEFERRED")
    r = _cli("approve_gate.py", name, "review", "APPROVED", "ok", "--by", REVIEWER)
    assert r.returncode == 0, r.stdout + r.stderr


def test_review_rejection_does_not_need_classification(review_run):
    name, _run = review_run
    r = _cli("approve_gate.py", name, "review", "REJECTED", "rehacer", "--by", REVIEWER)
    assert r.returncode == 0, r.stdout + r.stderr


def test_decide_findings_refuses_when_review_is_not_awaited(review_run):
    name, run = review_run
    st = json.loads((run / "factory-state.json").read_text())
    (run / "factory-state.json").write_text(json.dumps({**st, "awaiting_gate": "release"}))
    r = _cli("decide_findings.py", name, "--by", REVIEWER, "RF-01=ACCEPTED")
    assert r.returncode != 0 and not (run / "human-findings-decisions.json").exists()


def test_decide_findings_requires_a_by_value(review_run):
    name, _run = review_run
    r = _cli("decide_findings.py", name, "RF-01=ACCEPTED", "RF-02=ACCEPTED", "--by")
    assert r.returncode != 0 and "--by requiere un valor" in (r.stdout + r.stderr)


def test_decide_findings_rejects_unknown_ids(review_run):
    name, _run = review_run
    r = _cli("decide_findings.py", name, "--by", REVIEWER, "RF-09=ACCEPTED")
    assert r.returncode != 0 and "RF-09" in (r.stdout + r.stderr)


def test_decide_findings_records_reason_and_blocking_mark(review_run):
    name, run = review_run
    r = _cli("decide_findings.py", name, "--by", REVIEWER, "RF-02=ACCEPTED:blocking",
             "--reason", "RF-02=renombrar=ahora", "RF-01=ACCEPTED")
    assert r.returncode == 0, r.stdout + r.stderr
    data = {d["finding_id"]: d for d in json.loads(
        (run / "human-findings-decisions.json").read_text())["decisions"]}
    assert data["RF-02"]["blocking"] is True and data["RF-02"]["reason"] == "renombrar=ahora"
    assert data["RF-01"]["blocking"] is False and data["RF-01"]["recorded_by"] == REVIEWER
    r = _cli("decide_findings.py", name, "--by", REVIEWER, "RF-02=REJECTED:blocking")
    assert r.returncode != 0 and "ACCEPTED o MODIFIED" in (r.stdout + r.stderr)


def test_the_human_reviewer_can_add_findings(review_run):
    from factory_app import services
    from factory_app.project import load_project

    name, run = review_run
    project = load_project(ROOT)
    out = services.decide_findings(
        project, name, {}, REVIEWER,
        added=[{"severity": "MUST FIX", "title": "sync.js no se carga", "ac": "AC-03",
                "detail": "index.html | no incluye el script"}],
    )
    assert out["added"] == ["HR-01"]
    row = (run / "08b-human-review-findings.md").read_text(encoding="utf-8")
    assert "HR-01 | MUST FIX | AC-03 | - | sync.js no se carga: index.html / no incluye" in row
    assert f"(por {REVIEWER})" in row
    assert _decisions(run)["HR-01"] == "ACCEPTED"  # lo acepta quien lo añade
    assert "HR-01" in services.report_finding_ids(run)
    again = services.decide_findings(project, name, {}, REVIEWER,
                                     added=[{"severity": "SHOULD FIX", "title": "eco"}])
    assert again["added"] == ["HR-02"]
    with pytest.raises(services.FactoryError):
        services.decide_findings(project, name, {}, REVIEWER,
                                 added=[{"severity": "LOW", "title": "x"}])
    assert "HR-03" not in (run / "08b-human-review-findings.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- tools
@pytest.fixture()
def tools_root(tmp_path, monkeypatch):
    activate_project(tmp_path, monkeypatch)
    return tmp_path


@pytest.mark.parametrize(
    "name",
    [
        "human-release-decision.json",
        "human-findings-decisions.json",
        "factory-state.json",
        "factory-run.json",
        "traceability.md",
        "15-release-decision.json",
        "14-evidence-summary.md",
        "context-manifest.json",
    ],
)
def test_evidence_agent_cannot_forge_engine_or_human_artifacts(tools_root, name):
    out = tools.write_run_evidence("CR-T", name, '{"decision": "APPROVED"}')
    assert not out["ok"]
    assert not (tools_root / "runs" / "CR-T" / name).exists()


def test_evidence_agent_can_write_its_own_notes(tools_root):
    assert tools.write_run_evidence("CR-T", "evidence-agent-notes.md", "# notas")["ok"]


def test_read_repo_file_rejects_non_utf8(tools_root):
    (tools_root / "tests").mkdir()
    (tools_root / "tests" / "data.txt").write_bytes(b"\xff\xfe\x00bad")
    out = tools.read_repo_file("tests/data.txt")
    assert out == {"ok": False, "error": "file is not UTF-8 text"}


def test_list_repo_files_skips_pycache(tools_root):
    (tools_root / "tests" / "__pycache__").mkdir(parents=True)
    (tools_root / "tests" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (tools_root / "tests" / "test_a.py").write_text("x = 1\n")
    assert tools.list_repo_files("tests")["files"] == ["tests/test_a.py"]


# ---------------------------------------------------------------- start_run / export
def test_start_run_state_has_the_engine_shape(tmp_path, monkeypatch):
    mod = _script("start_run")
    (tmp_path / "change_requests").mkdir()
    (tmp_path / "change_requests" / "CR-T-x.md").write_text("# CR-T\n")
    activate_project(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["start_run.py", "CR-T"])
    mod.main()
    st = json.loads((tmp_path / "runs" / "CR-T" / "factory-state.json").read_text())
    for key in ("verification", "review", "security"):
        assert isinstance(st[key], dict) and st[key]["status"] == "NOT_RUN"
    run_json = ORCH.build_factory_run("CR-T", st, tmp_path / "runs" / "CR-T")
    assert isinstance(run_json["review"], dict) and isinstance(run_json["security"], dict)


def test_legacy_string_state_still_yields_dict_shape(tmp_path):
    st = {"status": "ESCALATED", "human_gates": {}, "review": "NOT_RUN", "security": "NOT_RUN"}
    run_json = ORCH.build_factory_run("CR-T", st, tmp_path)
    assert run_json["review"] == {"status": "NOT_RUN", "must_fix": 0, "should_fix": 0}


def test_escalated_run_can_be_exported(sandbox, monkeypatch):  # noqa: F811
    run = sandbox()
    drive(run, FakeExecutor(), decisions={"requirements": "REJECTED"})
    assert state(run)["status"] == "ESCALATED"
    mod = _script("export_evidence")
    root = run.parents[1]
    monkeypatch.setattr(sys, "argv", ["export_evidence.py", "CR-T"])
    mod.main()
    exported = {p.name for p in (root / "evidence" / "CR-T").iterdir()}
    assert {"factory-run.json", "traceability.md", "14-evidence-summary.md"} <= exported
