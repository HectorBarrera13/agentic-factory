"""Comportamiento del orquestador: gates bloqueantes, escalado, fix por ronda, evidencia.

Sandbox: el proyecto activo de la fabrica pasa a ser un repo temporal (misma config
que este repo); nunca se tocan runs/ reales. No requiere API key (FakeExecutor).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory_app import orchestrator as ORCH
from factory_app.context import project
from factory_app.orchestrator import AwaitingHuman, Blocked, FakeExecutor
from tests.factory.conftest import activate_project

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
    activate_project(tmp_path, monkeypatch)

    def make(change_id: str = "CR-T", cr_text: str = CR_CLEAN) -> Path:
        run = tmp_path / "runs" / change_id
        run.mkdir(parents=True)
        (run / "00-change-request.md").write_text(cr_text, encoding="utf-8")
        (run / "factory-state.json").write_text(json.dumps(STARTER_STATE), encoding="utf-8")
        return run

    return make


def approve(run: Path, gate: str, decision: str = "APPROVED") -> None:
    """Decision humana registrada AHORA (despues de la parada en el gate)."""
    (run / f"human-{gate}-decision.json").write_text(
        json.dumps(
            {
                "decision": decision,
                "reason": "ok",
                "recorded_by": "Tester (Tech Lead)",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )


def decide(run: Path, **verdicts: str) -> None:
    """Clasificacion por finding registrada AHORA (gate review abierto)."""
    (run / "human-findings-decisions.json").write_text(
        json.dumps(
            {
                "decisions": [
                    {"finding_id": fid.replace("_", "-"), "decision": v}
                    for fid, v in verdicts.items()
                ],
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )


def drive(run: Path, ex, stop_at: str | None = None, decisions: dict | None = None,
          findings: dict | None = None, max_steps: int = 25):
    """Avanza como lo haria un humano: en cada gate registra la decision DESPUES de la
    parada. `stop_at` devuelve la excepcion de ese gate sin decidirlo. `findings` se
    registra solo en la PRIMERA parada de review."""
    decisions = {
        "requirements": "APPROVED", "plan": "APPROVED", "review": "APPROVED",
        "release": "APPROVED", **(decisions or {}),
    }
    pending_findings = dict(findings or {})
    for _ in range(max_steps):
        try:
            return ORCH.advance("CR-T", ex)
        except AwaitingHuman as exc:
            if exc.gate == stop_at:
                return exc
            if state(run)["status"] == "ESCALATED" and not state(run).get("awaiting_gate"):
                return exc  # gate rechazado: nada que decidir
            if exc.gate == "review" and pending_findings:
                decide(run, **pending_findings)
                pending_findings = {}
            approve(run, exc.gate, decisions[exc.gate])
    raise AssertionError("drive: demasiados pasos")


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


def test_requirements_gate_is_never_skipped_on_resume(sandbox):
    """Regresion: sin decision del PO, reanudar NO salta al planner."""
    run = sandbox()
    for _ in range(3):
        with pytest.raises(AwaitingHuman) as exc:
            ORCH.advance("CR-T", FakeExecutor())
        assert exc.value.gate == "requirements"
    assert state(run)["status"] == "REQUIREMENTS_GATE"
    assert not (run / "03-implementation-plan.md").exists()


def test_pre_approval_is_ignored(sandbox):
    """Una decision registrada ANTES de que el run llegue al gate no vale."""
    run = sandbox()
    approve(run, "requirements")
    approve(run, "plan")  # pre-aprobacion: el plan ni existe
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", FakeExecutor())
    assert exc.value.gate == "requirements"
    approve(run, "requirements")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", FakeExecutor())
    assert exc.value.gate == "plan"  # la pre-aprobacion del plan se ignoro
    assert not (run / "05-coder-report.md").exists()


def test_decision_without_recorded_at_is_ignored(sandbox):
    run = sandbox()
    drive(run, FakeExecutor(), stop_at="requirements")
    (run / "human-requirements-decision.json").write_text(
        json.dumps({"decision": "APPROVED", "reason": "ok", "recorded_by": "X (Product Owner)"})
    )
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", FakeExecutor())
    assert exc.value.gate == "requirements"


class CountingExecutor(FakeExecutor):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls: dict[str, int] = {}

    def run_agent(self, phase, context):
        self.calls[phase] = self.calls.get(phase, 0) + 1
        return super().run_agent(phase, context)


def test_plan_is_not_regenerated_while_waiting_for_tech_lead(sandbox):
    run = sandbox()
    ex = CountingExecutor()
    drive(run, ex, stop_at="plan")
    approved_plan = (run / "03-implementation-plan.md").read_text()
    for _ in range(2):
        with pytest.raises(AwaitingHuman):
            ORCH.advance("CR-T", ex)
    assert ex.calls["plan"] == 1
    approve(run, "plan")
    drive(run, ex, stop_at="review")
    assert ex.calls["plan"] == 1  # se ejecuta el plan aprobado, no uno nuevo
    assert (run / "03-implementation-plan.md").read_text() == approved_plan


# ---------------------------------------------------------------- R4 verification FAIL
def test_verification_fail_never_reaches_review(sandbox):
    run = sandbox()
    ex = FakeExecutor(verify_fail=10)  # falla siempre
    with pytest.raises(Blocked):
        drive(run, ex)
    st = state(run)
    assert st["status"] == "ESCALATED"
    assert "verification FAIL" in st["stop_reason"]
    assert st["verify_rounds"] == ORCH.MAX_FIX_ROUNDS
    assert st["verification"]["status"] == "FAIL"
    assert not (run / "08-review-report.md").exists()  # JAMAS llega a reviewer con checks fallando
    assert (run / "factory-run.json").is_file()  # escalamiento exportable
    assert ORCH.advance("CR-T", ex)["status"] == "ESCALATED"  # terminal


def test_verification_fail_recovers_after_fix_round(sandbox):
    run = sandbox()
    ex = FakeExecutor(verify_fail=1)  # falla una vez, luego PASS
    drive(run, ex, stop_at="review")
    st = state(run)
    assert st["iterations"] == 1 and st["verification"]["status"] == "PASS"
    assert (run / "12-re-verification.txt").is_file()  # re-verification registrada


# ---------------------------------------------------------------- Gate 2 por finding
def test_findings_without_human_decisions_are_never_applied(sandbox):
    """Aunque existan MUST FIX, sin decision humana no cambia codigo (Gate 2 real)."""
    run = sandbox()
    ex = FakeExecutor(review_report="# Review\n\nMUST FIX: 1 (RF-01 carrera)\nSHOULD FIX: 0\n")
    exc = drive(run, ex, stop_at="release")
    assert exc.gate == "release"  # paso directo a evidencia: nadie autorizo fix
    assert not (run / "11-fix-report.md").exists()


def test_pre_recorded_findings_decisions_are_ignored(sandbox):
    run = sandbox()
    drive(run, FakeExecutor(), stop_at="plan")
    decide(run, RF_01="ACCEPTED")  # antes de que exista la revision
    ex = FakeExecutor(review_report="# Review\n\nRF-01 | MUST FIX | race\nMUST FIX: 1\n")
    exc = drive(run, ex, stop_at="release")
    assert exc.gate == "release"
    assert not (run / "11-fix-report.md").exists()


def test_accepted_blocking_finding_triggers_fix_and_new_review_round(sandbox):
    run = sandbox()
    ex = FakeExecutor(review_report="# Review\n\nMUST FIX: 1 (RF-01)\n")
    drive(run, ex, stop_at="review")
    decide(run, RF_01="ACCEPTED")
    approve(run, "review")
    with pytest.raises(AwaitingHuman) as exc:
        ORCH.advance("CR-T", ex)
    assert (run / "11-fix-report.md").is_file()  # Coder Fix autorizado
    assert exc.value.gate == "review"  # re-review exige decision NUEVA
    assert (run / "human-review-decision-round0.json").is_file()  # decision anterior archivada
    assert not (run / "human-findings-decisions.json").exists()
    assert state(run)["review_rounds"] == 1


@pytest.mark.parametrize("verdict", ["REJECTED", "DEFERRED"])
def test_review_gate_rejection_escalates(sandbox, verdict):
    run = sandbox()
    exc = drive(run, FakeExecutor(), decisions={"review": verdict})
    st = state(run)
    assert st["status"] == "ESCALATED" and st["stop_reason"] == f"gate review: {verdict}"
    assert st["human_gates"]["review"] == verdict
    assert not (run / "15-release-decision.json").exists()
    assert isinstance(exc, AwaitingHuman)


def test_review_gate_modified_proceeds(sandbox):
    run = sandbox()
    exc = drive(run, FakeExecutor(), decisions={"review": "MODIFIED"}, stop_at="release")
    assert exc.gate == "release"


class AlwaysMustFix(FakeExecutor):
    def run_agent(self, phase, context):
        if phase == "review":
            return {"report": "# Review\n\nRF-01 | MUST FIX | race\nMUST FIX: 1\n"}
        return super().run_agent(phase, context)


def test_review_fix_rounds_are_capped_and_escalate(sandbox):
    run = sandbox()
    ex = AlwaysMustFix()
    with pytest.raises(Blocked):
        for _ in range(ORCH.MAX_FIX_ROUNDS + 2):
            drive(run, ex, stop_at="review")
            decide(run, RF_01="ACCEPTED")
            approve(run, "review")
    st = state(run)
    assert st["status"] == "ESCALATED" and "findings bloqueantes" in st["stop_reason"]
    assert st["review_rounds"] == ORCH.MAX_FIX_ROUNDS
    assert st.get("verify_rounds", 0) == 0  # topes separados


# ---------------------------------------------------------------- Evidencia / shape §14
def test_full_happy_path_to_ready_for_human_merge(sandbox):
    run = sandbox()
    ex = FakeExecutor()
    final = drive(run, ex)
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
    drive(run, FakeExecutor(), decisions={"requirements": "REJECTED"})
    assert state(run)["status"] == "ESCALATED"
    assert not (run / "05-coder-report.md").exists()
    assert (run / "factory-run.json").is_file()


# ---------------------------------------------------------------- Git evidence
def work(rel: str, change_id: str = "CR-T") -> Path:
    """Ruta dentro del worktree del run (donde escriben los agentes, que crean carpetas)."""
    path = project().work_root(change_id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class CoderWrites(FakeExecutor):
    """Coder simulado: escribe producto en el worktree del run durante implement."""

    def __init__(self, rel: str, content: str, **kw):
        super().__init__(**kw)
        self.rel, self.content = rel, content

    def run_agent(self, phase, context):
        if phase == "implement":
            work(self.rel, context["change_id"]).write_text(self.content)
        return super().run_agent(phase, context)


def _git_repo(tmp: Path) -> str:
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("git no disponible")
    (tmp / "campusflow").mkdir(exist_ok=True)
    (tmp / "tests" / "product").mkdir(parents=True, exist_ok=True)
    (tmp / "campusflow" / "service.py").write_text("VALUE = 1\n")
    (tmp / ".gitignore").write_text("runs/\n.factory/\n")
    subprocess.run([git, "init", "-q", "-b", "main", str(tmp)], check=True)
    subprocess.run([git, "add", "-A"], cwd=tmp, check=True)
    subprocess.run(
        [git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
        cwd=tmp,
        check=True,
    )
    return git


def test_git_commit_and_diff_evidence_on_factory_branch(sandbox):
    """En repo git real, el implement cierra commit en factory/<CR> (en su worktree),
    nunca en main ni en el arbol del usuario."""
    run = sandbox()
    tmp = run.parents[1]
    git = _git_repo(tmp)
    ex = CoderWrites("campusflow/service.py", "VALUE = 2\n")
    exc = drive(run, ex, stop_at="review")  # se detiene sola en el gate de review
    assert exc.gate == "review"

    st = state(run)
    assert "## Git evidence" in (run / "05-coder-report.md").read_text()
    assert st["last_commit"] and st["worktree"] == ".factory/worktrees/CR-T"
    wt = tmp / st["worktree"]
    assert _sh(git, wt, "rev-parse", "--abbrev-ref", "HEAD") == "factory/CR-T"
    # el arbol del usuario sigue en main y sin el cambio de la maquina
    assert _sh(git, tmp, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (tmp / "campusflow" / "service.py").read_text() == "VALUE = 1\n"
    assert _sh(git, tmp, "show", "factory/CR-T:campusflow/service.py") == "VALUE = 2"


def _sh(git: str, tmp: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        [git, "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=tmp, capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_user_edits_in_their_tree_never_reach_the_run(sandbox):
    """El humano sigue editando su rama mientras corre el run: su trabajo no se mezcla en
    el commit de maquina ni se pierde (el run trabaja en su propio worktree)."""
    run = sandbox()
    tmp = run.parents[1]
    git = _git_repo(tmp)
    (tmp / "campusflow" / "service.py").write_text("VALUE = 99  # trabajo humano\n")
    drive(run, CoderWrites("campusflow/service.py", "VALUE = 2\n"), stop_at="review")
    assert (tmp / "campusflow" / "service.py").read_text() == "VALUE = 99  # trabajo humano\n"
    assert _sh(git, tmp, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _sh(git, tmp, "show", "factory/CR-T:campusflow/service.py") == "VALUE = 2"


def test_dirty_run_worktree_blocks_the_coder(sandbox):
    """Cambios sin commitear EN EL WORKTREE del run nunca se mezclan en el commit de maquina."""
    run = sandbox()
    tmp = run.parents[1]
    _git_repo(tmp)
    drive(run, FakeExecutor(), stop_at="review")  # rama + worktree creados, implement hecho
    # otra ronda de implementacion (fix) con el worktree sucio a mano
    ORCH._update_state(
        run, status="IMPLEMENT_PENDING", awaiting_gate="", fix_reason="verification FAIL"
    )
    work("campusflow/service.py").write_text("VALUE = 99  # edicion a mano en el worktree\n")
    with pytest.raises(Blocked, match="sin commitear"):
        ORCH.advance("CR-T", FakeExecutor())
    assert state(run)["status"] == "IMPLEMENT_PENDING"  # reanudable tras limpiar
    assert not (run / "11-fix-report.md").exists()


class CoderAndAnalystWrite(FakeExecutor):
    def __init__(self):
        super().__init__()
        self.ctx: dict[str, dict] = {}

    def run_agent(self, phase, context):
        self.ctx[phase] = context
        if phase == "implement":
            work("campusflow/service.py").write_text("VALUE = 2\n")
        if phase == "test_analysis":
            work("tests/product/test_value.py").write_text("def test_value():\n    assert True\n")
        return super().run_agent(phase, context)


def test_diffs_use_the_saved_base_even_without_main(sandbox):
    run = sandbox()
    tmp = run.parents[1]
    git = _git_repo(tmp)
    _sh(git, tmp, "branch", "-m", "main", "develop")  # no existe main
    base = _sh(git, tmp, "rev-parse", "HEAD")
    ex = CoderAndAnalystWrite()
    drive(run, ex, stop_at="review")
    st = state(run)
    assert st["base_commit"] == base and st["branch"] == "factory/CR-T"
    assert "+VALUE = 2" in ex.ctx["review"]["git_diff"]
    assert "+VALUE = 2" in ex.ctx["test_analysis"]["git_diff"]
    changed = ORCH._git(["diff", "--name-only", ORCH._run_diff_range(run)]).stdout.split()
    assert {"campusflow/service.py", "tests/product/test_value.py"} <= set(changed)
    # los tests del Test Analyst quedan comiteados: el worktree vuelve a estar limpio
    wt = work("")
    assert _sh(git, wt, "status", "--porcelain", "--", "campusflow", "tests/product") == ""
    assert "test_value.py" in _sh(git, wt, "show", "--stat", "HEAD")
    assert "## Git evidence" in (run / "06-test-analysis.md").read_text()
