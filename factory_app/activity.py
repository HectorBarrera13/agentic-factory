"""Actividad en vivo de un run: quien trabaja en que, en `<runs>/<CR>/activity.jsonl`.

Cada linea es un evento con datos reales del motor (inicio/fin de fase y agente, tools,
comandos de verificacion, gates, rondas de fix, pausas, escalados), nunca opinion de un LLM.
La UI la lee incrementalmente (`seq`) para animar el diagrama del flujo.

Sin run activo (`track`) `emit` no escribe nada: la CLI y los tests que no la usan no
cambian. Los argumentos de tools se reducen a `target` (ruta o 120 caracteres): nunca
contenidos completos ni secretos.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVITY_FILE = "activity.jsonl"
TARGET_CAP = 120
_PATH_KEYS = ("path", "relative_path", "file", "file_path", "target")

_current: ContextVar[Path | None] = ContextVar("factory_activity_run", default=None)
_lock = threading.Lock()
_seq: dict[Path, int] = {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def track(run_dir: Path) -> Iterator[None]:
    """Los `emit` sin run_dir explicito dentro de este bloque van a `run_dir`."""
    token = _current.set(run_dir)
    try:
        yield
    finally:
        _current.reset(token)


def current() -> Path | None:
    return _current.get()


def target_of(args: dict | None) -> str:
    """Resumen corto y seguro de los argumentos de una tool (la ruta si la hay)."""
    args = args or {}
    for key in _PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value[:TARGET_CAP]
    for value in args.values():
        if isinstance(value, str) and value:
            text = " ".join(value.split())
            return text[:TARGET_CAP] + ("…" if len(text) > TARGET_CAP else "")
    return ""


def _next_seq(path: Path) -> int:
    if path not in _seq:
        try:
            with path.open(encoding="utf-8") as fh:
                _seq[path] = sum(1 for _ in fh)
        except FileNotFoundError:
            _seq[path] = 0
    _seq[path] += 1
    return _seq[path]


def emit(kind: str, run_dir: Path | None = None, **fields: Any) -> dict | None:
    folder = run_dir or _current.get()
    if folder is None or not folder.is_dir():
        return None
    path = folder / ACTIVITY_FILE
    clean = {k: v for k, v in fields.items() if v is not None and v != ""}
    with _lock:
        if not path.exists():
            _seq.pop(path, None)  # archivado por Reintentar: la secuencia vuelve a empezar
        entry = {"seq": _next_seq(path), "ts": now(), "kind": kind, **clean}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read(run_dir: Path, after: int = 0, limit: int = 2000) -> dict:
    """Eventos con `seq > after` (como mucho `limit`) y el ultimo `seq` del archivo."""
    path = run_dir / ACTIVITY_FILE
    items: list[dict] = []
    last = 0
    if path.is_file():
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                last = max(last, int(entry.get("seq", 0)))
                if entry.get("seq", 0) > after and len(items) < limit:
                    items.append(entry)
    return {"items": items, "last": last}


def live(run_dir: Path) -> dict | None:
    """La fase abierta mas reciente (phase_start sin su fin), para el encabezado de la UI."""
    open_: dict[str, dict] = {}
    last_tool: dict[str, dict] = {}
    for e in read(run_dir)["items"]:
        phase = e.get("phase") or ""
        if e["kind"] == "phase_start":
            open_[phase] = e
            last_tool.pop(phase, None)
        elif e["kind"] in {"phase_end", "phase_error"}:
            open_.pop(phase, None)
        elif e["kind"] == "tool_start":
            last_tool[phase] = e
        elif e["kind"] in {"paused", "escalated", "done"}:
            open_.clear()
    if not open_:
        return None
    phase, start = max(open_.items(), key=lambda kv: kv[1]["seq"])
    tool = last_tool.get(phase)
    return {
        "phase": phase,
        "role": start.get("role"),
        "provider": start.get("provider"),
        "model": start.get("model"),
        "since": start["ts"],
        **({"tool": tool.get("tool"), "target": tool.get("target")} if tool else {}),
    }
