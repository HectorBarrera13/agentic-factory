"""Comportamiento del orquestador: gates bloqueantes, escalado, fix por ronda, evidencia.

Sandbox: se monkeypatcha REPO_ROOT del orchestrador hacia un repo temporal; nunca se
tocan runs/ reales. No requiere API key (FakeExecutor).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory_app import orchestrator as ORCH
from factory_app.orchestrator import AwaitingHuman, Blocked, FakeExecutor

STARTER_STATE = {
    "change_id": "CR-T",
    "status": "INTAKE_PENDING",
    "human_gates": {
        "requirements": "PENDING",
        "plan": "PENDING",
        "review": "PENDING",
        "release": "PENDING",
    },
    "verification": "NOT_RUN",
    "review": "NOT_RUN",
    "security": "NOT_RUN",
    "iterations": 0,
}

CR_CLEAN = "# CR-T\n\n- AC-CRT.1 algo\n- AC-CRT.2 otra\n"
CR_AMBIGUOUS = (
    "# CR-T\n\nSome users should receive priority. This request is intentionally ambiguous.\n"
)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(ORCH, "REPO_ROOT", tmp_path)

    def make(change_id: str = "CR-T", cr_text: str = CR_CLEAN) -> Path:
        run = tmp_path / "runs" / change_id
        run.mkdir(parents=True)
        (run / "00-change-request.md").write_text(cr_text, encoding="utf-8")
        (run / "factory-state.json").write_text(json.dumps(STARTER_STATE), encoding="utf-8")
        return run

    return make


def approve(run: Path, gate: str, decision: str = "APPROVED") -> None:
    (run / f"human-{gate}-decision.json").write_text(
        json.dumps({"decision": decision, "reason": "ok", "recorded_by": "Tester (Tech Lead)"}),
        encoding="utf-8",
    )


def state(run: Path) -> dict:
    return json.loads((run / "factory-state.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- E02 / R2
def test_ambiguous_cr_stops_at_gate_requirements(sandbox):
    run = sandbox(cr_text=CR_AMBIGUOUS)
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", FakeExecutor())
    assert state(run)["status"] == "ESCALATED"
    assert (run / "01-intake-report.md").is_file()
    assert not (run / "03-implementation-plan.md").exists()  # no inventa politica, no plan


# ---------------------------------------------------------------- R3 gates bloquean
def test_plan_blocked_until_po_and_tl_decide(sandbox):
    run = sandbox()
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", FakeExecutor())
    assert exc.value.gate == "requirements"
    assert not (run / "05-coder-report.md").exists()

    approve(run, "requirements")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", FakeExecutor())
    assert exc.value.gate == "plan"
    assert not (run / "05-coder-report.md").exists()  # Coder sigue bloqueado sin TL

    approve(run, "plan")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", FakeExecutor())
    assert exc.value.gate == "review"


# ---------------------------------------------------------------- R4 verification FAIL
def test_verification_fail_never_reaches_review(sandbox):
    run = sandbox()
    approve(run, "requirements")
    approve(run, "plan")
    ex = FakeExecutor(verify_fail=4)  # falla siempre
    with pytest.raises(Blocked):
        for _ in range(6):
            ORCH.advance("CR-T", ex)
    assert state(run)["status"] == "FIX_ROUNDS_EXCEEDED"
    assert state(run)["verification"]["status"] == "FAIL"
    assert not (run / "08-review-report.md").exists()  # JAMAS llega a reviewer con checks fallando


def test_verification_fail_recovers_after_fix_round(sandbox):
    run = sandbox()
    approve(run, "requirements")
    approve(run, "plan")
    ex = FakeExecutor(verify_fail=1)  # falla una vez, luego PASS
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", ex)
    st = state(run)
    assert st["iterations"] == 1 and st["verification"]["status"] == "PASS"
    assert (run / "12-re-verification.txt").is_file()  # re-verification registrada


# ---------------------------------------------------------------- Gate 2 por finding
def test_findings_without_human_decisions_are_never_applied(sandbox):
    """Aunque existan MUST FIX, sin decision humana no cambia codigo (Gate 2 real)."""
    run = sandbox()
    approve(run, "requirements")
    approve(run, "plan")
    approve(run, "review")
    ex = FakeExecutor(review_report="# Review\n\nMUST FIX: 1 (RF-01 carrera)\nSHOULD FIX: 0\n")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", ex)
    assert exc.value.gate == "release"  # paso directo a evidencia: nadie autorizo fix
    assert not (run / "11-fix-report.md").exists()


def test_accepted_blocking_finding_triggers_fix_and_new_review_round(sandbox):
    run = sandbox()
    approve(run, "requirements")
    approve(run, "plan")
    approve(run, "review")
    (run / "human-findings-decisions.json").write_text(
        json.dumps({"decisions": [{"finding_id": "RF-01", "decision": "ACCEPTED"}]}),
        encoding="utf-8",
    )
    ex = FakeExecutor(review_report="# Review\n\nMUST FIX: 1 (RF-01)\n")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", ex)
    assert (run / "11-fix-report.md").is_file()  # Coder Fix autorizado
    assert exc.value.gate == "review"  # re-review exige decision NUEVA
    assert (run / "human-review-decision-round0.json").is_file()  # decision anterior archivada
    assert not (run / "human-findings-decisions.json").exists()


# ---------------------------------------------------------------- Evidencia / shape §14
def test_full_happy_path_to_ready_for_human_merge(sandbox):
    run = sandbox()
    approve(run, "requirements")
    approve(run, "plan")
    approve(run, "review")
    ex = FakeExecutor()
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", ex)
    approve(run, "release")
    final = ORCH.advance("CR-T", ex)
    assert final["status"] == "READY FOR HUMAN MERGE"

    run_json = json.loads((run / "factory-run.json").read_text(encoding="utf-8"))
    assert set(run_json) >= {
        "change_id",
        "status",
        "requirements",
        "plan",
        "implementation",
        "test_analysis",
        "verification",
        "review",
        "security",
        "evidence",
        "human_gates",
    }
    assert set(run_json["verification"]) == {"status", "tests", "failed", "lint"}
    assert set(run_json["human_gates"]) == {
        "product_owner",
        "tech_lead",
        "reviewer",
        "release_manager",
    }
    assert run_json["human_gates"]["release_manager"] == "APPROVED"
    for artifact in (
        "14-evidence-summary.md",
        "traceability.md",
        "07-verification.txt",
        "08-review-report.md",
        "09-security-report.md",
    ):
        assert (run / artifact).is_file()
    assert "el merge final es exclusivamente humano" in (run / "traceability.md").read_text()


def test_rejected_gate_escalates(sandbox):
    run = sandbox()
    approve(run, "requirements", decision="REJECTED")
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", FakeExecutor())
    assert state(run)["status"] == "ESCALATED"
    assert not (run / "05-coder-report.md").exists()


# ---------------------------------------------------------------- Git evidence
def test_git_commit_and_diff_evidence_on_factory_branch(sandbox):
    """En repo git real, el implement cierra commit en factory/<CR>, nunca en main."""
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("git no disponible")
    run = sandbox()
    tmp = run.parents[1]
    (tmp / "campusflow").mkdir(exist_ok=True)
    (tmp / "tests" / "product").mkdir(parents=True, exist_ok=True)
    (tmp / "campusflow" / "service.py").write_text("VALUE = 1\n")
    subprocess.run([git, "init", "-q", "-b", "main", str(tmp)], check=True)
    subprocess.run([git, "add", "-A"], cwd=tmp, check=True)
    subprocess.run(
        [git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
        cwd=tmp,
        check=True,
    )

    approve(run, "requirements")
    approve(run, "plan")
    (tmp / "campusflow" / "service.py").write_text("VALUE = 2\n")  # coder simulado

    with pytest.raises(AwaitingHuman):  # se detiene sola en el gate de review
        ORCH.advance("CR-T", FakeExecutor())

    st = state(run)
    assert "## Git evidence" in (run / "05-coder-report.md").read_text()
    assert st["last_commit"]
    head = subprocess.run(
        [git, "rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp, capture_output=True, text=True
    ).stdout.strip()
    assert head == "factory/CR-T"  # el commit de maquina jamas toca main
