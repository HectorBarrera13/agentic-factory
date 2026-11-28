"""Control de un run en curso: pausa inmediata desde la UI.

El run que avanza en un hilo tiene un `RunControl` en un ContextVar (lo pone
`factory_ui.runs` con `use_control`). Pausar corta YA: cancela la llamada al modelo en
curso, interrumpe la espera de backoff y detiene el avance al inicio de la siguiente fase.
El estado queda en el ultimo checkpoint persistido (el *_PENDING de la fase cortada), que
`orchestrator.advance` sabe reanudar. La CLI no tiene control: nada cambia ahi.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class Paused(Exception):  # noqa: N818 - es un evento de control, no un error
    """El humano pauso el run; el estado vuelve al ultimo checkpoint."""


class RunControl:
    def __init__(self) -> None:
        self._pause = threading.Event()

    def pause(self) -> None:
        self._pause.set()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    def wait(self, seconds: float) -> bool:
        """Espera hasta `seconds`; True si llego una pausa (corta la espera)."""
        return self._pause.wait(seconds)


_current: ContextVar[RunControl | None] = ContextVar("run_control", default=None)


@contextmanager
def use_control(ctl: RunControl) -> Iterator[RunControl]:
    token = _current.set(ctl)
    try:
        yield ctl
    finally:
        _current.reset(token)


def pause_requested() -> bool:
    ctl = _current.get()
    return bool(ctl and ctl.paused)


def check_pause(where: str = "") -> None:
    if pause_requested():
        raise Paused(where or "pausado")


def sleep(seconds: float) -> None:
    """time.sleep interrumpible por la pausa (sin control: duerme normal)."""
    ctl = _current.get()
    if ctl is None:
        import time

        time.sleep(seconds)
        return
    if ctl.wait(seconds):
        raise Paused("pausado durante la espera de reintento")
