"""Probe de modelo: ¿este modelo funciona con mi clave/plan y sabe usar tools?

Hace UNA llamada real minima por el mismo camino que los agentes (build_llm + LlmAgent de
ADK) con una tool trivial `ping`. Cuesta pocos tokens. Resultado:

- OK           el modelo respondio y llamo a la tool
- NO_TOOLS     respondio pero no llamo a la tool (o no soporta function calling)
- NO_ACCESS    el modelo no existe o tu plan no lo permite (403/404)
- INVALID_KEY  clave invalida (401)
- QUOTA        sin cuota / rate limit (429)
- ERROR        otro problema (mensaje incluido)

Ademas `temperature_supported`: si el proveedor rechaza el parametro temperature, se
reintenta sin el y se marca False (los agentes lo omitiran para ese modelo).
Los resultados se guardan en `~/.agentic-factory/model-probes.json` (sin claves).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time

from . import credentials
from .roles import FactoryError

FILE = "model-probes.json"
TIMEOUT = 60.0
PROMPT = "Call the `ping` tool exactly once, then answer with the single word OK."

_lock = threading.Lock()
version = 0  # cambia con cada resultado nuevo o probe iniciado (lo mira el SSE de la UI)
running: set[str] = set()  # claves provider:model con un probe en curso


def _key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


def _path():
    return credentials.home() / FILE


def results() -> dict:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def result(provider: str, model: str) -> dict | None:
    return results().get(_key(provider, model))


def _store(provider: str, model: str, outcome: dict) -> None:
    global version
    with _lock:
        data = results()
        data[_key(provider, model)] = outcome
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{FILE}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
        version += 1


def _mark(provider: str, model: str, active: bool) -> None:
    global version
    with _lock:
        (running.add if active else running.discard)(_key(provider, model))
        version += 1


def temperature_supported(provider: str, model: str) -> bool | None:
    hit = result(provider, model)
    return None if hit is None else hit.get("temperature_supported")


_SECRET_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{4}|AIza[0-9A-Za-z_-]{4})[0-9A-Za-z_*.-]{4,}")


def redact(text: str, provider: str | None = None) -> str:
    """Quita claves de mensajes de error (algunos proveedores repiten parte de la clave)."""
    key = credentials.get_key(provider) if provider else None
    if key:
        text = text.replace(key, "***")
    return _SECRET_RE.sub(r"\1***", text)


# ---------------------------------------------------------------- clasificacion
_RULES = (
    ("INVALID_KEY", ("401", "authentication", "api key not valid", "invalid api key",
                     "invalid_api_key", "incorrect api key", "invalid x-api-key", "unauthorized",
                     "falta la api key")),
    ("QUOTA", ("429", "quota", "resource_exhausted", "rate limit", "ratelimit",
               "insufficient_quota", "overloaded", "529")),
    ("NO_ACCESS", ("403", "404", "not found", "notfound", "permission", "does not exist",
                   "not have access", "model_not_found", "no access", "not available")),
)


def is_temperature_error(text: str) -> bool:
    t = text.lower()
    return "temperature" in t and any(
        m in t for m in ("unsupported", "not support", "does not support", "only the default",
                         "deprecated", "not allowed", "invalid")
    )


def classify(exc: BaseException) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if ("tool" in text or "function" in text) and ("support" in text or "not enabled" in text):
        return "NO_TOOLS"
    for kind, markers in _RULES:
        if any(m in text for m in markers):
            return kind
    return "ERROR"


# ---------------------------------------------------------------- ejecucion
def _run_once(llm, temperature: float | None) -> bool:
    """Devuelve True si el modelo llamo a la tool. Propaga errores del proveedor."""
    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    called = {"n": 0}

    def ping() -> dict:
        """Health check used to verify tool calling.

        Returns:
            dict: {"pong": True}
        """
        called["n"] += 1
        return {"pong": True}

    config = types.GenerateContentConfig(
        **({} if temperature is None else {"temperature": temperature})
    )
    agent = LlmAgent(name="ModelProbe", model=llm, instruction=PROMPT, tools=[ping],
                     generate_content_config=config)

    async def go():
        runner = InMemoryRunner(agent=agent, app_name="factory_probe")
        try:
            session = await runner.session_service.create_session(
                app_name=runner.app_name, user_id="probe")
            msg = types.Content(role="user", parts=[types.Part(text="ping")])
            async for _event in runner.run_async(user_id="probe", session_id=session.id,
                                                 new_message=msg):
                if called["n"]:
                    break
        finally:
            await runner.close()

    asyncio.run(asyncio.wait_for(go(), TIMEOUT))
    return called["n"] > 0


def probe(provider: str, model: str, llm=None, temperature: float | None = 0.2) -> dict:
    """Prueba (proveedor, modelo) y guarda el resultado. `llm` inyectable en tests."""
    from .models import build_llm

    _mark(provider, model, True)
    try:
        return _probe(provider, model, llm, temperature, build_llm)
    finally:
        _mark(provider, model, False)


def _probe(provider, model, llm, temperature, build_llm) -> dict:
    started = time.time()
    temp_ok: bool | None = None if temperature is None else True
    responded = False  # solo si el modelo contesto sabemos algo de su temperatura
    try:
        llm = llm if llm is not None else build_llm(provider, model)
        try:
            called = _run_once(llm, temperature)
        except Exception as exc:  # noqa: BLE001 - se clasifica para la UI
            if temperature is None or not is_temperature_error(str(exc)):
                raise
            temp_ok = False
            called = _run_once(llm, None)
        responded = True
        status, message = ("OK", "responde y usa tools") if called else (
            "NO_TOOLS", "respondio sin llamar a la tool: no sirve para los agentes")
    except FactoryError as exc:
        status, message = classify(exc), str(exc)
        if status == "ERROR":
            status = "INVALID_KEY" if "api key" in str(exc).lower() else "ERROR"
    except TimeoutError:
        status, message = "ERROR", f"sin respuesta en {int(TIMEOUT)} s"
    except Exception as exc:  # noqa: BLE001
        status, message = classify(exc), f"{type(exc).__name__}: {str(exc)[:300]}"
    outcome = {
        "provider": provider,
        "model": model,
        "status": status,
        "message": redact(message, provider),
        "temperature_supported": temp_ok if responded else None,
        "at": time.time(),
        "seconds": round(time.time() - started, 1),
    }
    _store(provider, model, outcome)
    return outcome


def probe_in_background(pairs: list[tuple[str, str]], max_age: float = 3600) -> list[str]:
    """Prueba en un hilo los pares sin un resultado OK reciente (los fallos se reintentan:
    pudo cambiar la clave, el plan o el servidor). Devuelve las claves lanzadas."""
    now = time.time()

    def fresh(hit: dict | None) -> bool:
        return bool(hit) and hit.get("status") == "OK" and now - float(hit.get("at", 0)) <= max_age

    pending = [(p, m) for p, m in pairs if _key(p, m) not in running and not fresh(result(p, m))]

    def work():
        for p, m in pending:
            probe(p, m)

    if pending:
        threading.Thread(target=work, daemon=True, name="model-probe").start()
    return [_key(p, m) for p, m in pending]
