"""Verificacion determinista, guardia de integridad de tests y escaneres (sin API)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from factory_app import orchestrator as ORCH
from factory_app import scanners, tools
from factory_app.orchestrator import Blocked, FakeExecutor
from tests.factory.test_orchestrator import (
    _git_repo,
    _sh,
    approve,
    decide,
    drive,
    sandbox,  # noqa: F401  (fixture)
    state,
)


# ---------------------------------------------------------------- parseo
def _v(*results):
    return {"ok": False, "results": list(results)}


def test_pytest_errors_count_as_failed_but_ruff_errors_do_not():
    v = _v(
        {"command": "python -m pytest -q", "stdout": "3 passed, 2 errors in 0.1s"},
        {"command": "python -m ruff check", "returncode": 1, "stdout": "Found 1 error."},
    )
    tests, failed, lint = ORCH.parse_verification(v)
    assert (tests, failed, lint) == (5, 2, "FAIL")


def test_xfailed_is_not_failed():
    v = _v({"command": "pytest", "stdout": "82 passed, 1 xfailed in 1s"})
    assert ORCH.parse_verification(v)[1] == 0


@pytest.mark.parametrize(
    ("text", "medium"),
    [
        ("# Security\n\nHIGH: 0\nMEDIUM: 0\n", 0),
        ("SEC-01 | MEDIUM | x\nSEC-02 | MEDIUM | y\nHIGH: 0\nMEDIUM: 2\n", 2),
        ("SEC-01 | MEDIUM | x\n", 1),  # sin linea resumen: se cuentan filas
    ],
)
def test_medium_count_uses_the_summary_line(text, medium):
    assert ORCH.count_findings(text)["medium"] == medium


# ---------------------------------------------------------------- run_baseline_verification
def test_verification_uses_the_running_interpreter(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    assert tools.run_baseline_verification()["ok"]
    assert all(cmd[0] == sys.executable for cmd in seen)


def test_verification_timeout_is_a_fail_not_a_crash(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    out = tools.run_baseline_verification()
    assert out["ok"] is False
    assert all("timeout" in r["stderr"] for r in out["results"])


# ---------------------------------------------------------------- integridad de tests
BASE_TESTS = (
    "import pytest\n\n"
    "def test_one():\n    assert True\n\n"
    "@pytest.mark.xfail(strict=True)\n"
    "def test_two():\n    assert False\n"
)


@pytest.fixture()
def git_sandbox(sandbox):  # noqa: F811
    run = sandbox()
    tmp = run.parents[1]
    git = _git_repo(tmp)
    (tmp / "tests" / "product" / "test_a.py").write_text(BASE_TESTS)
    _sh(git, tmp, "add", "-A")
    _sh(git, tmp, "commit", "-qm", "tests")
    return run, tmp, {"base_commit": _sh(git, tmp, "rev-parse", "HEAD")}


def test_integrity_ok_when_tests_are_untouched(git_sandbox):
    _run, _tmp, st = git_sandbox
    assert ORCH.check_test_integrity(st)["returncode"] == 0


def test_integrity_detects_removed_test(git_sandbox):
    _run, tmp, st = git_sandbox
    (tmp / "tests" / "product" / "test_a.py").write_text(BASE_TESTS.split("@pytest")[0])
    out = ORCH.check_test_integrity(st)
    assert out["returncode"] == 1 and "test_two" in out["stderr"]


def test_integrity_detects_new_skip(git_sandbox):
    _run, tmp, st = git_sandbox
    (tmp / "tests" / "product" / "test_a.py").write_text(
        BASE_TESTS.replace("def test_one", "@pytest.mark.skip\ndef test_one")
    )
    assert ORCH.check_test_integrity(st)["returncode"] == 1


def test_integrity_detects_skip_in_new_test_file(git_sandbox):
    _run, tmp, st = git_sandbox
    (tmp / "tests" / "product" / "test_new.py").write_text(
        "import pytest\n\ndef test_x():\n    pytest.skip('later')\n"
    )
    assert ORCH.check_test_integrity(st)["returncode"] == 1


def test_integrity_allows_removing_an_xfail(git_sandbox):
    """CR-003: arreglar el defecto exige quitar el xfail strict; eso no es debilitar."""
    _run, tmp, st = git_sandbox
    (tmp / "tests" / "product" / "test_a.py").write_text(
        BASE_TESTS.replace("@pytest.mark.xfail(strict=True)\n", "")
    )
    assert ORCH.check_test_integrity(st)["returncode"] == 0


class CoderDeletesTest(FakeExecutor):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def run_agent(self, phase, context):
        if phase in {"implement", "fix"}:
            self.path.write_text("def test_one():\n    assert True\n")
        return super().run_agent(phase, context)


def test_weakened_tests_fail_verification_and_never_reach_review(git_sandbox):
    run, tmp, _st = git_sandbox
    with pytest.raises(Blocked):
        drive(run, CoderDeletesTest(tmp / "tests" / "product" / "test_a.py"))
    verification = (run / "07-verification.txt").read_text()
    assert "tests eliminados" in verification and "test_two" in verification
    assert state(run)["status"] == "ESCALATED"
    assert not (run / "08-review-report.md").exists()


# ---------------------------------------------------------------- escaneres
def _ok(stdout):
    return lambda cmd, root, timeout=300: {
        "tool": cmd[0], "status": "OK", "returncode": 1, "stdout": stdout, "stderr": "boom",
    }


@pytest.mark.parametrize("scan", ["semgrep_scan", "pip_audit_scan", "secret_scan"])
def test_unreadable_scanner_output_is_parse_error(monkeypatch, tmp_path, scan):
    monkeypatch.setattr(scanners, "_run", _ok("{not json"))
    out = getattr(scanners, scan)(tmp_path)
    assert out["status"] == "PARSE_ERROR" and out["findings"] == []


def test_parse_error_is_reported_as_degraded(monkeypatch, tmp_path):
    monkeypatch.setattr(scanners, "_run", _ok("{not json"))
    result = scanners.run_security_scans(tmp_path)
    assert set(result["degraded_scanners"]) == {"semgrep", "pip-audit", "secrets"}
    assert result["blocked"] is False


def _fake_scans(high: int):
    findings = [
        {"rule": f"rule-{i}", "severity": "HIGH", "file": "campusflow/api.py", "line": i}
        for i in range(1, high + 1)
    ]
    return {"scans": [{"scanner": "semgrep", "status": "OK", "findings": findings}]}


def test_high_scanner_findings_get_classifiable_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(scanners, "semgrep_scan", lambda root: _fake_scans(2)["scans"][0])
    monkeypatch.setattr(scanners, "pip_audit_scan", lambda root: {"scanner": "pip-audit",
                                                                  "status": "OK", "findings": []})
    monkeypatch.setattr(scanners, "secret_scan", lambda root: {"scanner": "secrets",
                                                               "status": "OK", "findings": []})
    result = scanners.run_security_scans(tmp_path)
    ids = [f["id"] for f in result["scans"][0]["findings"]]
    assert ids == ["SEC-SCAN-01", "SEC-SCAN-02"] and result["blocked"]


def test_accepted_scanner_finding_is_blocking_and_triggers_fix(sandbox, monkeypatch):  # noqa: F811
    real = scanners.run_security_scans
    calls = {"n": 0}

    def one_high_then_clean(root):
        calls["n"] += 1
        if calls["n"] == 1:
            finding = {"rule": "sqli", "severity": "HIGH", "file": "campusflow/api.py",
                       "line": 7, "id": "SEC-SCAN-01"}
            return {"scans": [{"scanner": "semgrep", "status": "OK", "findings": [finding]}],
                    "high": 1, "blocked": True, "degraded_scanners": []}
        return {"scans": [], "high": 0, "blocked": False, "degraded_scanners": []}

    monkeypatch.setattr(scanners, "run_security_scans", one_high_then_clean)
    run = sandbox()
    drive(run, FakeExecutor(), stop_at="review")
    report = (run / "09-security-report.md").read_text()
    assert "SEC-SCAN-01 | HIGH | - | campusflow/api.py:7 | sqli (semgrep)" in report
    assert ORCH.finding_severities(run)["SEC-SCAN-01"] == "HIGH"
    assert state(run)["security"]["status"] == "BLOCKED"
    decide(run, SEC_SCAN_01="ACCEPTED")
    approve(run, "review")
    drive(run, FakeExecutor(), stop_at="review")
    assert (run / "11-fix-report.md").is_file()
    assert "SEC-SCAN-01" in json.loads(
        (run / "factory-state.json").read_text()
    )["last_fix_reason"]
    assert real is not scanners.run_security_scans


@pytest.mark.parametrize("scan", ["semgrep_scan", "pip_audit_scan", "secret_scan"])
def test_empty_scanner_output_is_parse_error_not_ok(monkeypatch, tmp_path, scan):
    monkeypatch.setattr(scanners, "_run", _ok(""))
    assert getattr(scanners, scan)(tmp_path)["status"] == "PARSE_ERROR"


@pytest.mark.real_scanner_run
def test_scanner_output_is_decoded_as_utf8(monkeypatch, tmp_path):
    """En Windows el locale (cp1252) rompia la lectura de la salida de semgrep."""
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(scanners.shutil, "which", lambda name: name)
    monkeypatch.setattr(scanners.subprocess, "run", fake_run)
    scanners._run(["semgrep", "scan"], tmp_path)
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
