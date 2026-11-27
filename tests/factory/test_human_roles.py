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


def _cli_run(name: str, state: dict):
    run = ROOT / "runs" / name
    run.mkdir(parents=True)
    (run / "factory-state.json").write_text(json.dumps(state))
    return run


def _cleanup(run):
    for f in run.iterdir():
        f.unlink()
    run.rmdir()


def _approve(name, gate, by):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "approve_gate.py"), name, gate, "APPROVED",
         "ok", "--by", by],
        capture_output=True, text=True, check=False,
    )


def test_approve_gate_cli_rejects_gate_not_awaited():
    """No hay pre-aprobaciones: solo se registra el gate en el que el run esta detenido."""
    name = "CR-AWAIT-TEST"
    run = _cli_run(name, {"status": "REQUIREMENTS_GATE", "awaiting_gate": "requirements",
                          "human_gates": {"plan": "PENDING"}})
    try:
        r = _approve(name, "plan", "Luis (Tech Lead)")
        assert r.returncode != 0 and "no esta esperando" in (r.stderr + r.stdout)
        assert not (run / "human-plan-decision.json").exists()
    finally:
        _cleanup(run)


def test_approve_gate_cli_records_awaited_gate_without_touching_state():
    name = "CR-AWAIT-OK"
    st = {"status": "PLAN_PENDING", "awaiting_gate": "plan", "human_gates": {"plan": "PENDING"}}
    run = _cli_run(name, st)
    try:
        r = _approve(name, "plan", "Luis (Tech Lead)")
        assert r.returncode == 0, r.stderr
        decision = json.loads((run / "human-plan-decision.json").read_text())
        assert decision["decision"] == "APPROVED" and decision["recorded_at"]
        # human_gates lo escribe el motor al consumir la decision
        assert json.loads((run / "factory-state.json").read_text()) == st
    finally:
        _cleanup(run)
