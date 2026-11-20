"""Carga .env ANTES de cualquier import de agentes (GEMINI_MODEL se resuelve al importar)."""
from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

from .agent import root_agent

__all__ = ["root_agent"]
