"""Carga .env ANTES de cualquier import de agentes (el modelo se resuelve al construirlos).

`root_agent` / `app` (playground de ADK) se importan perezosamente: la CLI, los servicios y
Factory Studio no pagan el costo de cargar google-adk si no corren agentes.
"""
from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

__all__ = ["app", "root_agent"]


def __getattr__(name: str):
    if name in {"root_agent", "app"}:
        from . import agent

        return getattr(agent, name)
    raise AttributeError(f"module 'factory_app' has no attribute {name!r}")
