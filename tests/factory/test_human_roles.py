"""§9: cada gate solo lo firma el rol humano que le corresponde."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _roles():
    spec = importlib.util.spec_from_file_location("_roles", ROOT / "scripts" / "_roles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("gate", "by"),
    [
        ("requirements", "Ana (Product Owner)"),
        ("plan", "Luis (Tech Lead)"),
        ("plan", "Ana (Product Owner)"),  # §10: "Product Owner y/o Tech Lead"
        ("review", "Ana (Human Reviewer)"),
        ("findings", "Ana (Human Reviewer)"),
        ("release", "Hector (Release Manager)"),
    ],
)
def test_authorized_roles_pass(gate, by):
    assert _roles().validate_role(gate, by)


@pytest.mark.parametrize(
    ("gate", "by"),
    [
        ("release", "Hector (Tech Lead)"),
        ("requirements", "Hector (Release Manager)"),
        ("review", "Hector"),  # sin rol
        ("plan", "Hector (Human Reviewer)"),
    ],
)
def test_unauthorized_role_is_rejected(gate, by):
    with pytest.raises(SystemExit):
        _roles().validate_role(gate, by)


def test_approve_gate_cli_rejects_wrong_role_and_records_nothing(tmp_path):
    run = ROOT / "runs" / "CR-ROLE-TEST"
    run.mkdir(parents=True)
    try:
        (run / "factory-state.json").write_text(
            json.dumps({"status": "INTAKE_PENDING", "human_gates": {"release": "PENDING"}})
        )
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "approve_gate.py"), "CR-ROLE-TEST",
             "release", "APPROVED", "ok", "--by", "Hector (Tech Lead)"],
            capture_output=True, text=True, check=False,
        )
        assert r.returncode != 0 and "Rol no autorizado" in (r.stderr + r.stdout)
        assert not (run / "human-release-decision.json").exists()
    finally:
        for f in run.iterdir():
            f.unlink()
        run.rmdir()
