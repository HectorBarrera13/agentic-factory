"""Construye el LLM de ADK para (proveedor, modelo).

- gemini            -> google.adk Gemini (cliente genai con 1 intento)
- anthropic         -> google.adk AnthropicLlm (API directa de Anthropic)
- openai            -> google.adk LiteLlm("openai/<modelo>")
- ollama            -> google.adk LiteLlm("ollama_chat/<modelo>", api_base=<url>)
- openai_compatible -> google.adk LiteLlm("openai/<modelo>", api_base=<url>)

Imports perezosos: el modo fake y la CLI no cargan anthropic/litellm. Las claves salen de
factory_app.credentials (entorno real > ~/.agentic-factory/credentials.json).

Los SDKs van con 0 reintentos propios: cada modelo se envuelve en `request_retry.RequestRetry`,
que repite la PETICION que choco con un 429/503 (contada en la metrica); el backoff por fase
del orquestador queda para lo que ese reintento no resuelve.
"""

from __future__ import annotations

import types
from functools import cache

from . import credentials
from .providers import info
from .roles import FactoryError


def _missing(provider: str, package: str) -> FactoryError:
    return FactoryError(
        f"el proveedor {provider} necesita el paquete {package!r}: ejecuta `uv sync`"
    )


@cache
def _retrying(cls: type) -> type:
    from .request_retry import RequestRetry

    return types.new_class(f"Retrying{cls.__name__}", (RequestRetry, cls))


def build_llm(provider: str, model: str):
    info(provider)  # FactoryError si no existe
    if not model or not model.strip():
        raise FactoryError(f"falta el modelo para el proveedor {provider}")
    credentials.apply_to_env()
    if provider == "gemini":
        from google.adk.models.google_llm import Gemini
        from google.genai import types

        return _retrying(Gemini)(model=model, retry_options=types.HttpRetryOptions(attempts=1))
    if provider == "anthropic":
        try:
            from google.adk.models.anthropic_llm import AnthropicLlm
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise _missing(provider, "anthropic") from exc
        key = credentials.get_key(provider)
        if not key:
            raise FactoryError("falta la API key de Anthropic (Ajustes > Proveedores)")
        from anthropic import AsyncAnthropic

        # max_retries=0: reintenta RequestRetry (por peticion) y, detras, el orquestador
        return _retrying(AnthropicLlm)(
            model=model, client=AsyncAnthropic(api_key=key, max_retries=0)
        )
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise _missing(provider, "litellm") from exc
    if provider == "openai":
        key = credentials.get_key(provider)
        if not key:
            raise FactoryError("falta la API key de OpenAI (Ajustes > Proveedores)")
        return _retrying(LiteLlm)(model=f"openai/{model}", api_key=key, num_retries=0)
    url = credentials.get_url(provider)
    if not url:
        raise FactoryError(f"falta la URL del servidor para {provider} (Ajustes > Proveedores)")
    if provider == "ollama":
        return _retrying(LiteLlm)(model=f"ollama_chat/{model}", api_base=url, num_retries=0)
    base = url if url.endswith("/v1") else f"{url}/v1"
    return _retrying(LiteLlm)(model=f"openai/{model}", api_base=base,
                   api_key=credentials.get_key(provider) or "local", num_retries=0)
