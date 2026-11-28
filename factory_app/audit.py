"""Auditoria de tools ADK: cada llamada de un agente queda en <runs>/<CR>/tool-calls.jsonl.

Los callbacks leen `change_id` y `phase` del estado de la sesion (los pone ADKExecutor al
crearla). Sin `change_id` valido no se escribe nada. Los callbacks nunca alteran la
respuesta de la tool (devuelven None).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

from . import activity
from .context import project

AUDIT_FILE = "tool-calls.jsonl"
ARG_CAP = 300  # el contenido completo ya queda en el diff/artefactos; aqui solo la traza


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > ARG_CAP:
        return value[:ARG_CAP] + f"...[TRUNCATED {len(value) - ARG_CAP} chars]"
    return value


def _append(tool_context, entry: dict) -> None:
    try:
        folder = project().run_dir(str(tool_context.state.get("change_id") or ""))
    except ValueError:  # sin run valido (sesion de playground, id raro): no se escribe nada
        return
    folder.mkdir(parents=True, exist_ok=True)
    line = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "phase": tool_context.state.get("phase"),
        "agent": tool_context.agent_name,
        **entry,
    }
    with (folder / AUDIT_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


_started: dict[tuple, float] = {}
_started_lock = threading.Lock()


def _run_folder(tool_context):
    try:
        return project().run_dir(str(tool_context.state.get("change_id") or ""))
    except ValueError:
        return None


def _live(tool_context, kind: str, tool, args: dict, **extra) -> None:
    folder = _run_folder(tool_context)
    if folder is None:
        return
    activity.emit(
        kind, folder, phase=tool_context.state.get("phase"), agent=tool_context.agent_name,
        tool=tool.name, target=activity.target_of(args), **extra,
    )


def _duration(tool_context, tool) -> int | None:
    with _started_lock:
        t0 = _started.pop((id(tool_context), tool.name), None)
    return None if t0 is None else int((time.monotonic() - t0) * 1000)


def record_tool_start(tool, args: dict, tool_context) -> None:
    """Actividad en vivo: la UI muestra la tool mientras corre (no altera la llamada)."""
    with _started_lock:
        _started[(id(tool_context), tool.name)] = time.monotonic()
    _live(tool_context, "tool_start", tool, args)
    return None


def record_tool_call(tool, args: dict, tool_context, tool_response) -> None:
    response = tool_response if isinstance(tool_response, dict) else {}
    _live(tool_context, "tool_end", tool, args, ok=response.get("ok"),
          error=_short(response.get("error")), duration_ms=_duration(tool_context, tool))
    _append(
        tool_context,
        {
            "tool": tool.name,
            "args": {k: _short(v) for k, v in (args or {}).items()},
            "ok": response.get("ok"),
            "error": _short(response.get("error")),
        },
    )
    return None


def record_tool_error(tool, args: dict, tool_context, error: Exception) -> None:
    _live(tool_context, "tool_end", tool, args, ok=False,
          error=_short(f"{type(error).__name__}: {error}"),
          duration_ms=_duration(tool_context, tool))
    _append(
        tool_context,
        {
            "tool": tool.name,
            "args": {k: _short(v) for k, v in (args or {}).items()},
            "ok": False,
            "error": _short(f"{type(error).__name__}: {error}"),
        },
    )
    return None
