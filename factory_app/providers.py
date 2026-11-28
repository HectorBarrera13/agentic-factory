"""Proveedores de modelos: catalogo, prueba de conexion y listado dinamico de modelos.

El listado sale del endpoint de cada proveedor (no de una lista escrita a mano), asi un
modelo nuevo aparece solo. Solo se habla con el host oficial o con la URL local que puso
el usuario; la clave nunca se registra ni se devuelve.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

from . import credentials
from .roles import FactoryError


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    needs_key: bool
    needs_url: bool
    temperature_range: tuple[float, float]
    description: str


PROVIDERS: dict[str, ProviderInfo] = {
    "gemini": ProviderInfo("gemini", "Google Gemini", True, False, (0.0, 2.0),
                           "Gemini Developer API (Google AI Studio)."),
    "anthropic": ProviderInfo("anthropic", "Anthropic", True, False, (0.0, 1.0),
                              "Modelos Claude por la API de Anthropic."),
    "openai": ProviderInfo("openai", "OpenAI", True, False, (0.0, 2.0),
                           "Modelos GPT por la API de OpenAI."),
    "ollama": ProviderInfo("ollama", "Ollama (local)", False, True, (0.0, 2.0),
                           "Modelos locales servidos por Ollama."),
    "openai_compatible": ProviderInfo(
        "openai_compatible", "Servidor compatible con OpenAI", False, True, (0.0, 2.0),
        "LM Studio, vLLM, llama.cpp server u otro endpoint /v1 compatible (clave opcional).",
    ),
}
PROVIDER_IDS = tuple(PROVIDERS)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
ANTHROPIC_URL = "https://api.anthropic.com/v1/models"
OPENAI_URL = "https://api.openai.com/v1/models"
# Modelos de OpenAI que no sirven para agentes de texto.
OPENAI_EXCLUDE = ("embedding", "tts", "whisper", "dall-e", "image", "moderation", "realtime",
                  "audio", "transcribe", "search", "davinci", "babbage", "sora")

CACHE_SECONDS = 600
_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()
# Inyectable en tests (httpx.MockTransport); en uso real queda None.
transport: httpx.BaseTransport | None = None


def info(provider: str) -> ProviderInfo:
    try:
        return PROVIDERS[provider]
    except KeyError as exc:
        raise FactoryError(f"proveedor desconocido: {provider!r}") from exc


def _client() -> httpx.Client:
    return httpx.Client(timeout=15.0, transport=transport, follow_redirects=False)


class ConnectionProblem(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _get(url: str, headers: dict | None = None, params: dict | None = None) -> dict:
    try:
        with _client() as client:
            r = client.get(url, headers=headers or {}, params=params or {})
    except httpx.TimeoutException as exc:
        raise ConnectionProblem("UNREACHABLE", "tiempo de espera agotado") from exc
    except httpx.HTTPError as exc:
        raise ConnectionProblem("UNREACHABLE", f"no se pudo conectar: {type(exc).__name__}") \
            from exc
    if r.status_code in (401, 403):
        raise ConnectionProblem("INVALID_KEY", "clave invalida o sin permiso")
    if r.status_code >= 400:
        raise ConnectionProblem("ERROR", f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as exc:
        raise ConnectionProblem("ERROR", "respuesta no es JSON") from exc


def _require_key(provider: str) -> str:
    key = credentials.get_key(provider)
    if not key:
        raise ConnectionProblem("NO_KEY", "falta la API key")
    return key


def _require_url(provider: str) -> str:
    url = credentials.get_url(provider)
    if not url:
        raise ConnectionProblem("NO_URL", "falta la URL del servidor")
    return url


def _fetch(provider: str) -> list[dict]:
    if provider == "gemini":
        key, models, token = _require_key(provider), [], None
        for _ in range(20):  # paginado
            params = {"pageSize": 1000, **({"pageToken": token} if token else {})}
            data = _get(GEMINI_URL, {"x-goog-api-key": key}, params)
            for m in data.get("models", []):
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                mid = str(m.get("name", "")).removeprefix("models/")
                models.append({"id": mid, "label": m.get("displayName") or mid})
            token = data.get("nextPageToken")
            if not token:
                break
        return models
    if provider == "anthropic":
        key, models, after = _require_key(provider), [], None
        for _ in range(20):
            params = {"limit": 1000, **({"after_id": after} if after else {})}
            data = _get(ANTHROPIC_URL, {"x-api-key": key, "anthropic-version": "2023-06-01"},
                        params)
            for m in data.get("data", []):
                models.append({"id": m["id"], "label": m.get("display_name") or m["id"],
                               "created": m.get("created_at")})
            if not data.get("has_more"):
                break
            after = data.get("last_id")
        return models
    if provider == "openai":
        data = _get(OPENAI_URL, {"Authorization": f"Bearer {_require_key(provider)}"})
        return [
            {"id": m["id"], "label": m["id"], "created": m.get("created")}
            for m in data.get("data", [])
            if not any(x in m["id"].lower() for x in OPENAI_EXCLUDE)
        ]
    if provider == "ollama":
        data = _get(f"{_require_url(provider)}/api/tags")
        return [{"id": m["name"], "label": m["name"]} for m in data.get("models", [])]
    if provider == "openai_compatible":
        key = credentials.get_key(provider)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        base = _require_url(provider)
        data = _get(f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models", headers)
        return [{"id": m["id"], "label": m["id"]} for m in data.get("data", [])]
    raise FactoryError(f"proveedor desconocido: {provider!r}")


def list_models(provider: str, refresh: bool = False) -> list[dict]:
    """Modelos de generacion de texto del proveedor (cache 10 min)."""
    info(provider)
    with _cache_lock:
        hit = _cache.get(provider)
        if hit and not refresh and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    models = sorted(_fetch(provider), key=lambda m: m["id"])
    with _cache_lock:
        _cache[provider] = (time.time(), models)
    return models


def clear_cache(provider: str | None = None) -> None:
    with _cache_lock:
        if provider:
            _cache.pop(provider, None)
        else:
            _cache.clear()


def test_connection(provider: str) -> dict:
    """Una llamada de listado: dice si la clave/URL funciona y cuantos modelos ve."""
    info(provider)
    try:
        models = list_models(provider, refresh=True)
    except ConnectionProblem as exc:
        return {"ok": False, "kind": exc.kind, "message": str(exc), "models": 0}
    return {"ok": True, "kind": "OK", "message": f"{len(models)} modelos", "models": len(models)}


def effective_temperature(provider: str, value: float | None,
                          supported: bool | None = True) -> float | None:
    """Temperatura que realmente se envia: recortada al rango nativo del proveedor, o
    None (no se envia) si el probe detecto que el modelo no la acepta."""
    if value is None or supported is False:
        return None
    lo, hi = info(provider).temperature_range
    return min(max(float(value), lo), hi)
