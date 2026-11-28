"""Escalado sin bucles: aclaraciones del PO acumuladas y con tope, y Reintentar (nuevo
intento del mismo CR editado) tras ESCALATED / NOT READY.

Solo FakeExecutor / repos temporales: no requiere API key ni toca runs/ reales.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from factory_app import orchestrator as ORCH
from factory_app import services
from factory_app.context import project
from factory_app.orchestrator import AwaitingHuman, Blocked, FakeExecutor
from factory_app.roles import FactoryError
from tests.factory.test_alignment import LATER, human
from tests.factory.test_orchestrator import (
    CR_AMBIGUOUS,
    CR_CLEAN,
    _git_repo,
    drive,
    sandbox,  # noqa: F401  (fixture)
    state,
)

PO = "Ana (Product Owner)"


class AlwaysAsks(FakeExecutor):
    """Intake que nunca queda satisfecho: pide aclaracion siempre (el bucle a cortar)."""

    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []

    def run_agent(self, phase, context):
        if phase == "intake":
            self.prompts.append(context.get("po_clarification", ""))
            return {"report": "# Intake\n", "recommendation": "HUMAN_CLARIFICATION_REQUIRED"}
        return super().run_agent(phase, context)


def _clarify(run, ex, times):
    for i in range(times):
        human(run, "requirements", reason=f"respuesta {i + 1}", recorded_at=LATER)
        with pytest.raises((AwaitingHuman, Blocked)):
            ORCH.advance("CR-T", ex)


# ---------------------------------------------------------------- aclaraciones
def test_clarifications_accumulate_in_the_prompt(sandbox):  # noqa: F811
    run = sandbox()
    ex = AlwaysAsks()
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", ex)
    _clarify(run, ex, 2)
    # el 3er intake ve las DOS respuestas anteriores, no solo la ultima
    assert "respuesta 1" in ex.prompts[-1] and "respuesta 2" in ex.prompts[-1]
    assert [c["n"] for c in state(run)["po_clarifications"]] == [1, 2]
    assert state(run)["awaiting_gate"] == "requirements"  # aun reanudable


def test_clarification_cap_escalates_for_good(sandbox):  # noqa: F811
    run = sandbox()
    ex = AlwaysAsks()
    cap = project().config.limits.clarification_rounds
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", ex)
    _clarify(run, ex, cap)
    st = state(run)
    assert st["status"] == "ESCALATED"
    assert st["stop_reason"].startswith(f"aclaraciones agotadas ({cap}/{cap})")
    assert not st.get("awaiting_gate")
    # una decision nueva ya NO reanuda: solo queda Reintentar
    human(run, "requirements", recorded_at=LATER)
    assert ORCH.advance("CR-T", ex)["status"] == "ESCALATED"
    assert len(ex.prompts) == cap + 1


# ---------------------------------------------------------------- reintentar
def _cr_file(text=CR_CLEAN):
    p = project()
    p.cr_dir.mkdir(parents=True, exist_ok=True)
    path = p.cr_dir / "CR-T-prueba.md"
    path.write_text(text, encoding="utf-8")
    return path


def _reject_plan(run):
    drive(run, FakeExecutor(), decisions={"plan": "REJECTED"})
    assert state(run)["status"] == "ESCALATED"


def test_retry_after_rejected_gate_starts_a_fresh_attempt(sandbox):  # noqa: F811
    run = sandbox()
    cr = _cr_file()
    _reject_plan(run)
    old_files = sorted(p.name for p in run.iterdir() if p.is_file())
    new_cr = CR_CLEAN + "- AC-CRT.3 alcance aclarado\n"
    st = services.retry_run(project(), "CR-T", new_cr, "el plan no cubria AC-CRT.3", PO)
    assert st["status"] == "INTAKE_PENDING" and st["attempt"] == 2
    assert set(st["human_gates"].values()) == {"PENDING"}
    assert st["retry"]["cr_changed"] is True and st["retry"]["role"] == "Product Owner"
    prev = st["previous_attempts"][0]
    assert prev["n"] == 1 and prev["stop_reason"] == "gate plan: REJECTED"
    archive = run / "intentos" / "1"
    assert sorted(p.name for p in archive.iterdir()) == old_files  # intacto
    assert (run / "00-change-request.md").read_text(encoding="utf-8") == new_cr
    assert cr.read_text(encoding="utf-8") == new_cr  # el CR del usuario se actualiza
    assert (run / "00-retry-decision.md").is_file()
    # el intento nuevo vuelve a pasar por TODOS los gates
    final = drive(run, FakeExecutor())
    assert final["status"] == "READY FOR HUMAN MERGE"
    run_json = json.loads((run / "factory-run.json").read_text(encoding="utf-8"))
    assert run_json["attempt"] == 2 and run_json["previous_attempts"][0]["n"] == 1
    assert [r["change_id"] for r in services.list_runs(project())] == ["CR-T"]


def test_retry_after_round_cap_and_not_ready(sandbox):  # noqa: F811
    run = sandbox()
    _cr_file()
    with pytest.raises(Blocked):
        drive(run, FakeExecutor(verify_fail=99))  # topes de fix de verificacion
    assert state(run)["status"] == "ESCALATED"
    services.retry_run(project(), "CR-T", CR_CLEAN, "reintento tras arreglar la base", PO)
    drive(run, FakeExecutor(), decisions={"release": "REJECTED"})
    assert state(run)["status"] == "NOT READY"
    st = services.retry_run(project(), "CR-T", CR_CLEAN, "release rechazado", PO)
    assert st["attempt"] == 3 and st["retry"]["cr_changed"] is False
    assert [a["status"] for a in st["previous_attempts"]] == ["ESCALATED", "NOT READY"]
    assert (run / "intentos" / "2" / "factory-state.json").is_file()


@pytest.mark.parametrize(
    "why",
    ["active", "ready", "no_reason", "not_po"],
)
def test_retry_refusals(sandbox, why):  # noqa: F811
    run = sandbox()
    _cr_file()
    if why == "ready":
        drive(run, FakeExecutor())
    elif why != "active":
        _reject_plan(run)
    reason = "" if why == "no_reason" else "motivo"
    by = "Leo (Tech Lead)" if why == "not_po" else PO
    with pytest.raises(FactoryError):
        services.retry_run(project(), "CR-T", CR_CLEAN, reason, by)
    assert not (run / "intentos").exists()


def test_attempt_cap_needs_explicit_force(sandbox):  # noqa: F811
    run = sandbox()
    _cr_file()
    cap = project().config.limits.attempts
    for _ in range(cap - 1):
        _reject_plan(run)
        services.retry_run(project(), "CR-T", CR_CLEAN, "otra vez", PO)
    _reject_plan(run)
    with pytest.raises(FactoryError) as exc:
        services.retry_run(project(), "CR-T", CR_CLEAN, "otra vez", PO)
    assert exc.value.code == "attempts_exhausted"
    st = services.retry_run(project(), "CR-T", CR_CLEAN, "decido seguir", PO, force=True)
    assert st["attempt"] == cap + 1 and st["retry"]["forced"] is True
    assert "superado" in (run / "00-retry-decision.md").read_text(encoding="utf-8")


def test_retry_keeps_the_previous_branch(sandbox):  # noqa: F811
    run = sandbox()
    tmp = run.parents[1]
    git = _git_repo(tmp)
    _cr_file()
    drive(run, FakeExecutor(), decisions={"review": "REJECTED"})  # rama ya creada
    branch = state(run)["branch"]
    wt = project().worktree("CR-T")
    assert wt.exists()
    # colision: ya existe un -intento1 de antes => usa el siguiente numero
    subprocess.run([git, "branch", f"{branch}-intento1", "main"], cwd=tmp, check=True)
    st = services.retry_run(project(), "CR-T", CR_CLEAN, "review rechazado", PO)
    assert st["previous_attempts"][0]["branch"] == f"{branch}-intento2"
    assert not wt.exists()

    def exists(ref):
        return subprocess.run(
            [git, "rev-parse", "--verify", f"refs/heads/{ref}"], cwd=tmp, capture_output=True
        ).returncode == 0

    assert exists(f"{branch}-intento2") and not exists(branch)
    final = drive(run, FakeExecutor())  # el intento nuevo recrea la rama desde HEAD
    assert final["status"] == "READY FOR HUMAN MERGE" and exists(branch)


def test_ambiguous_escalation_can_also_be_retried(sandbox):  # noqa: F811
    run = sandbox(cr_text=CR_AMBIGUOUS)
    _cr_file(CR_AMBIGUOUS)
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-T", FakeExecutor())
    st = services.retry_run(project(), "CR-T", CR_CLEAN, "reescribo el CR sin ambiguedad", PO)
    assert st["status"] == "INTAKE_PENDING" and not st.get("po_clarifications")
    assert drive(run, FakeExecutor())["status"] == "READY FOR HUMAN MERGE"
