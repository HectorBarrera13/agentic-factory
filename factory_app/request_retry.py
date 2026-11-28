"""Reintento POR PETICION al modelo ante 429/503 (cuota por minuto, alta demanda).

Una fase ADK hace muchas peticiones (una por cada llamada a tool). Con un free tier de
15 peticiones/min, el backoff por fase del orquestador repetia la fase entera desde cero y
volvia a chocar con el mismo limite: el Coder nunca terminaba. Aqui se espera y se repite
solo la peticion que fallo; la fase conserva lo que ya hizo.

- Respeta `RetryInfo.retryDelay` del proveedor, con un minimo exponencial (4s, 8s, 16s...,
  tope 60s): un limite por minuto se libera dentro de esa ventana.
- Cuota diaria (`retryDelay` > 10 min) o reintentos agotados: el error sube al backoff del
  orquestador, que decide (TransientModelError, run resumible). Los SDKs siguen con 0
  reintentos propios: este es el unico reintento por peticion y se cuenta en la metrica.
- Solo si el modelo aun no devolvio nada: una respuesta en streaming a medias no se repite.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncGenerator
from contextvars import ContextVar

REQUEST_RETRIES = 6
REQUEST_BACKOFF = 4.0
REQUEST_MAX_WAIT = 60.0
DAILY_QUOTA_HINT = 600

# reintentos por peticion de la fase en curso (lista mutable: sobrevive a asyncio.run)
_retries: ContextVar[list[int] | None] = ContextVar("request_retries", default=None)


def start_counting() -> list[int]:
    counter = [0]
    _retries.set(counter)
    return counter


def wait_seconds(attempt: int, hint: int | None) -> float:
    floor = min(REQUEST_MAX_WAIT, REQUEST_BACKOFF * (2 ** (attempt - 1)))
    return max(float(hint or 0), floor) + random.uniform(0, 1)


class RequestRetry:
    """Mixin para un BaseLlm de ADK: `class RetryingGemini(RequestRetry, Gemini)`."""

    async def generate_content_async(  # type: ignore[override]
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator:
        from .orchestrator import _is_transient, _retry_hint_seconds

        attempt = 0
        while True:
            produced = False
            try:
                async for response in super().generate_content_async(llm_request, stream):
                    produced = True
                    yield response
                return
            except Exception as exc:
                hint = _retry_hint_seconds(exc)
                if (
                    produced
                    or not _is_transient(exc)
                    or (hint is not None and hint > DAILY_QUOTA_HINT)
                    or attempt >= REQUEST_RETRIES
                ):
                    raise
                attempt += 1
                if (counter := _retries.get()) is not None:
                    counter[0] += 1
                delay = wait_seconds(attempt, hint)
                print(f"  [llm] peticion: transitorio ({type(exc).__name__}), "
                      f"reintento {attempt}/{REQUEST_RETRIES} en {delay:.1f}s", flush=True)
                await asyncio.sleep(delay)  # la pausa cancela la tarea: corta la espera
