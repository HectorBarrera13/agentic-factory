"""Pausa inmediata y reanudacion de un run (control desde la UI).

La pausa corta YA (llamada al modelo en curso, espera de backoff o entre fases) y deja el
run en su ultimo checkpoint (*_PENDING). Reanudar repite la fase cortada y llega al mismo
resultado que un run sin pausa.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from factory_app import control
from factory_app import orchestrator as ORCH
from factory_app.orchestrator import FakeExecutor
from tests.factory.conftest import activate_project
from tests.factory.test_orchestrator import (
    CR_CLEAN,
    STARTER_STATE,
    CoderWrites,
    _git_repo,
    approve,
    drive,
    state,
    work,
)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Mismo sandbox que test_orchestrator: proyecto temporal con un run CR-T."""
    activate_project(tmp_path, monkeypatch)

    def make(change_id: str = "CR-T"):
        run = tmp_path / "runs" / change_id
        run.mkdir(parents=True)
        (run / "00-change-request.md").write_text(CR_CLEAN, encoding="utf-8")
        (run / "factory-state.json").write_text(json.dumps(STARTER_STATE), encoding="utf-8")
        return run

    return make


class PauseDuring(FakeExecutor):
    """Pulsa "Pausar" mientras corre `phase` (la fase termina; el corte llega al checkpoint)."""

    def __init__(self, ctl: control.RunControl, phase: str, **kw):
        super().__init__(**kw)
        self.ctl, self.phase, self.calls = ctl, phase, []

    def run_agent(self, phase, context):
        self.calls.append(phase)
        if phase == self.phase and not self.ctl.paused and self.phase not in self.calls[:-1]:
            self.ctl.pause()
        return super().run_agent(phase, context)


def _events(run):
    return [t.get("event") for t in state(run).get("transitions", []) if t.get("event")]


def test_pause_stops_at_the_next_checkpoint_and_records_it(sandbox):
    run = sandbox()
    ctl = control.RunControl()
    with control.use_control(ctl), pytest.raises(control.Paused):
        ORCH.advance("CR-T", PauseDuring(ctl, "intake"))
    st = state(run)
    assert st["paused"]["status"] == st["status"]  # el status no cambia: es el checkpoint
    assert st["status"] == "REQUIREMENTS_GATE"  # intake termino; se corta antes del gate
    assert _events(run) == ["PAUSED"]
    # reanudar (sin pausa) limpia el estado especial y sigue
    with pytest.raises(ORCH.AwaitingHuman):
        ORCH.advance("CR-T", FakeExecutor())
    assert "paused" not in state(run)
    assert _events(run) == ["PAUSED", "RESUMED"]


def test_resume_reaches_the_same_result_as_a_run_without_pause(sandbox):
    run = sandbox()
    for phase in ("plan", "implement", "review"):
        ctl = control.RunControl()
        ex = PauseDuring(ctl, phase)
        with control.use_control(ctl), pytest.raises((control.Paused, ORCH.AwaitingHuman)):
            for _ in range(10):
                try:
                    ORCH.advance("CR-T", ex)
                except ORCH.AwaitingHuman as exc:
                    approve(run, exc.gate)
        assert "paused" in state(run), phase
    final = drive(run, FakeExecutor())
    assert final["status"] == "READY FOR HUMAN MERGE"
    assert "paused" not in state(run)
    assert _events(run).count("PAUSED") == 3 and _events(run).count("RESUMED") == 3


class CutCoder(CoderWrites):
    """Coder cortado a media escritura (lo que hace la cancelacion del ADK)."""

    def __init__(self, ctl, **kw):
        super().__init__("campusflow/service.py", "VALUE = 'a medias'\n", **kw)
        self.ctl = ctl

    def run_agent(self, phase, context):
        if phase == "implement":
            work(self.rel, context["change_id"]).write_text(self.content)
            work("campusflow/nuevo.py", context["change_id"]).write_text("x = 1\n")
            self.ctl.pause()
            raise control.Paused("pausado durante implement")
        return super().run_agent(phase, context)


def test_pause_in_implement_discards_partial_writes_and_resume_works(sandbox):
    run = sandbox()
    tmp = run.parents[1]
    _git_repo(tmp)
    drive(run, FakeExecutor(), stop_at="plan")
    approve(run, "plan")
    ctl = control.RunControl()
    with control.use_control(ctl), pytest.raises(control.Paused):
        ORCH.advance("CR-T", CutCoder(ctl))
    st = state(run)
    assert st["status"] == "IMPLEMENT_PENDING"
    assert st["paused"]["worktree_reset"] is True
    assert work("campusflow/service.py").read_text() == "VALUE = 1\n"  # vuelve al inicio
    assert not work("campusflow/nuevo.py").exists()
    assert (tmp / "campusflow" / "service.py").read_text() == "VALUE = 1\n"  # arbol del usuario
    out = drive(run, CoderWrites("campusflow/service.py", "VALUE = 2\n"), stop_at="review")
    assert isinstance(out, ORCH.AwaitingHuman)
    assert work("campusflow/service.py").read_text() == "VALUE = 2\n"


def test_pause_cuts_the_backoff_wait():
    ctl = control.RunControl()
    threading.Timer(0.1, ctl.pause).start()
    t0 = time.monotonic()
    with control.use_control(ctl), pytest.raises(control.Paused):
        control.sleep(30)
    assert time.monotonic() - t0 < 2


def test_without_control_nothing_changes():
    control.check_pause()  # la CLI no tiene control: nunca pausa
    control.sleep(0)


def test_pause_cancels_a_hanging_model_call(monkeypatch, tmp_path):
    pytest.importorskip("google.adk")
    from google.adk.models.base_llm import BaseLlm

    from factory_app import agents

    started = threading.Event()

    class HangingLlm(BaseLlm):
        model: str = "hang"

        async def generate_content_async(self, llm_request, stream=False):  # noqa: ARG002
            started.set()
            await asyncio.sleep(60)
            yield  # pragma: no cover

    real = agents._agent

    def with_hang(*args, **kwargs):
        agent = real(*args, **kwargs)
        agent.model = HangingLlm()
        return agent

    monkeypatch.setattr(agents, "_agent", with_hang)
    activate_project(tmp_path, monkeypatch)
    ctl = control.RunControl()
    threading.Thread(target=lambda: started.wait(10) and ctl.pause(), daemon=True).start()
    t0 = time.monotonic()
    with control.use_control(ctl), pytest.raises(control.Paused, match="plan"):
        ORCH.ADKExecutor().run_agent("plan", {"change_id": "CR-001", "cr_text": "x"})
    assert started.is_set()
    assert time.monotonic() - t0 < 5
