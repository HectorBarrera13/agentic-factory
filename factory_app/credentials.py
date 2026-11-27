"""Credenciales de proveedores de modelos, fuera de cualquier proyecto.

`~/.agentic-factory/credentials.json` (o `FACTORY_HOME`): por proveedor `api_key` y, para
servidores locales, `url`. Reglas:
- Una variable de entorno REAL gana al archivo (el `.env` del repo sigue funcionando porque
  se carga al entorno). Lo que viene del entorno no se edita ni se borra desde la UI.
- La clave nunca se devuelve: `status()` solo dice si hay, de donde viene y sus 4 ultimos.
- `apply_to_env()` deja las claves del archivo en el entorno del proceso (los SDK las leen
  de ahi) sin pisar variables reales.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .roles import FactoryError

FILE = "credentials.json"

# proveedor -> (variables de clave por prioridad, variable de url)
ENV_VARS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "gemini": (("GEMINI_API_KEY", "GOOGLE_API_KEY"), None),
    "anthropic": (("ANTHROPIC_API_KEY",), None),
    "openai": (("OPENAI_API_KEY",), None),
    "ollama": ((), "OLLAMA_HOST"),
    "openai_compatible": (("OPENAI_COMPATIBLE_API_KEY",), "OPENAI_COMPATIBLE_BASE_URL"),
}
DEFAULT_URLS = {"ollama": "http://localhost:11434"}

_lock = threading.Lock()
# variables que pusimos nosotros desde el archivo (no son "reales" del entorno)
_applied: dict[str, str] = {}


def home() -> Path:
    return Path(os.environ.get("FACTORY_HOME") or Path.home() / ".agentic-factory")


def _path() -> Path:
    return home() / FILE


def _known(provider: str) -> None:
    if provider not in ENV_VARS:
        raise FactoryError(f"proveedor desconocido: {provider!r}")


def _read() -> dict:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{FILE}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # POSIX; en Windows el perfil del usuario ya restringe el acceso
    except OSError:  # pragma: no cover
        pass
    tmp.replace(path)


def _env_value(var: str) -> str | None:
    """Valor real del entorno (ignora lo que pusimos desde el archivo)."""
    value = os.environ.get(var)
    if not value or _applied.get(var) == value:
        return None
    return value


def _validate_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise FactoryError("la URL debe empezar con http:// o https://")
    return url


def get_key(provider: str) -> str | None:
    _known(provider)
    for var in ENV_VARS[provider][0]:
        if value := _env_value(var):
            return value
    return (_read().get(provider) or {}).get("api_key") or None


def get_url(provider: str) -> str | None:
    _known(provider)
    var = ENV_VARS[provider][1]
    if var and (value := _env_value(var)):
        return value.rstrip("/")
    return (_read().get(provider) or {}).get("url") or DEFAULT_URLS.get(provider)


def _source(provider: str, field: str) -> str:
    keys, url_var = ENV_VARS[provider]
    names = keys if field == "api_key" else ((url_var,) if url_var else ())
    if any(_env_value(v) for v in names):
        return "env"
    return "file" if (_read().get(provider) or {}).get(field) else "none"


def status(provider: str) -> dict:
    """Estado sin secretos: configurado, origen, 4 ultimos caracteres y url."""
    _known(provider)
    key = get_key(provider)
    return {
        "has_key": bool(key),
        "key_source": _source(provider, "api_key"),
        "last4": key[-4:] if key and len(key) >= 8 else ("••••" if key else None),
        "url": get_url(provider),
        "url_source": _source(provider, "url"),
    }


def save(provider: str, api_key: str | None = None, url: str | None = None) -> dict:
    _known(provider)
    keys, url_var = ENV_VARS[provider]
    with _lock:
        data = _read()
        entry = dict(data.get(provider) or {})
        if api_key is not None:
            if not keys:
                raise FactoryError(f"{provider} no usa API key")
            if any(_env_value(v) for v in keys):
                raise FactoryError("la clave viene de una variable de entorno; cambiala ahi")
            api_key = api_key.strip()
            if not api_key or any(c.isspace() for c in api_key):
                raise FactoryError("API key vacia o con espacios")
            entry["api_key"] = api_key
        if url is not None:
            if not url_var:
                raise FactoryError(f"{provider} no usa URL")
            entry["url"] = _validate_url(url)
        data[provider] = entry
        _write(data)
    apply_to_env()
    return status(provider)


def delete_key(provider: str) -> dict:
    _known(provider)
    if _source(provider, "api_key") == "env":
        raise FactoryError("la clave viene de una variable de entorno; no se borra desde la UI")
    with _lock:
        data = _read()
        entry = dict(data.get(provider) or {})
        entry.pop("api_key", None)
        data[provider] = entry
        _write(data)
    apply_to_env()
    return status(provider)


def apply_to_env() -> None:
    """Expone las claves del archivo en el entorno del proceso (sin pisar las reales)."""
    data = _read()
    with _lock:
        for provider, (keys, url_var) in ENV_VARS.items():
            entry = data.get(provider) or {}
            pairs = [(keys[0], entry.get("api_key"))] if keys else []
            if url_var:
                pairs.append((url_var, entry.get("url")))
            for var, value in pairs:
                current = os.environ.get(var)
                ours = var in _applied and _applied[var] == current
                if current and not ours:
                    continue  # variable real: manda el entorno
                if value:
                    os.environ[var] = value
                    _applied[var] = value
                elif ours:
                    os.environ.pop(var, None)
                    _applied.pop(var, None)
