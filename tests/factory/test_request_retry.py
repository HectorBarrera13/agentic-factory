"""Reintento por peticion: un 429 por minuto repite SOLO la peticion, no la fase entera."""

from __future__ import annotations

import asyncio

import pytest

from factory_app import models, request_retry


class _Base:
    """Hace de BaseLlm: falla las primeras `fails` peticiones con el error dado."""

    def __init__(self, fails: int, error: str, partial: bool = False):
        self.fails, self.error, self.partial, self.calls = fails, error, partial, 0

    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        if self.partial and self.calls == 1:
            yield "medio"
        if self.calls <= self.fails:
            raise RuntimeError(self.error)
        yield "respuesta"


class _Llm(request_retry.RequestRetry, _Base):
    pass


RPM = "429 RESOURCE_EXHAUSTED free_tier_requests, limit: 15 'retryDelay': '3s'"
DAILY = "429 RESOURCE_EXHAUSTED per day 'retryDelay': '20000s'"


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(request_retry.asyncio, "sleep", fake_sleep)
    return waits


def _collect(llm) -> list:
    async def run():
        return [r async for r in llm.generate_content_async(object())]

    return asyncio.run(run())


def test_a_per_minute_429_repeats_only_that_request_and_counts_it(_no_wait):
    llm = _Llm(fails=2, error=RPM)
    counter = request_retry.start_counting()
    assert _collect(llm) == ["respuesta"]
    assert llm.calls == 3 and counter == [2]
    # respeta el retryDelay (3s) con un minimo exponencial: 4s, 8s
    assert 4 <= _no_wait[0] < 5 and 8 <= _no_wait[1] < 9


def test_waits_are_capped_and_follow_the_provider_hint():
    assert request_retry.wait_seconds(1, 30) >= 30
    assert request_retry.wait_seconds(9, None) <= request_retry.REQUEST_MAX_WAIT + 1


@pytest.mark.parametrize(
    ("error", "fails"),
    [
        (DAILY, 1),  # cuota diaria: decide el orquestador (no gastar cuota esperando)
        ("KeyError: plantilla", 1),  # no transitorio
        (RPM, request_retry.REQUEST_RETRIES + 1),  # agotado: sube al backoff por fase
    ],
)
def test_what_a_request_retry_cannot_fix_goes_up(error, fails):
    llm = _Llm(fails=fails, error=error)
    with pytest.raises(RuntimeError):
        _collect(llm)
    expected = request_retry.REQUEST_RETRIES + 1 if error == RPM else 1
    assert llm.calls == expected


def test_a_half_streamed_answer_is_not_repeated():
    llm = _Llm(fails=1, error=RPM, partial=True)
    with pytest.raises(RuntimeError):
        _collect(llm)
    assert llm.calls == 1


def test_every_provider_model_gets_the_request_retry(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    llm = models.build_llm("gemini", "gemini-3.5-flash-lite")
    assert isinstance(llm, request_retry.RequestRetry)
    assert llm.retry_options.attempts == 1  # el SDK no reintenta por su cuenta
