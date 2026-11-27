"""Auditoria de tools ADK: cada llamada de un agente queda en <runs>/<CR>/tool-calls.jsonl.

Los callbacks leen `change_id` y `phase` del estado de la sesion (los pone ADKExecutor al
crearla). Sin `change_id` valido no se escribe nada. Los callbacks nunca alteran la
respuesta de la tool (devuelven None).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

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


def record_tool_call(tool, args: dict, tool_context, tool_response) -> None:
    response = tool_response if isinstance(tool_response, dict) else {}
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
