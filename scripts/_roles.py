"""Compatibilidad CLI: los roles viven en factory_app.roles; aqui los errores salen como
SystemExit (mensaje y codigo distinto de cero)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory_app.project import DEFAULT_GATE_ROLES  # noqa: E402
from factory_app.roles import FactoryError, role_of  # noqa: E402
from factory_app.roles import validate_role as _validate  # noqa: E402

GATE_ROLES = {gate: tuple(roles) for gate, roles in DEFAULT_GATE_ROLES.items()}
__all__ = ["GATE_ROLES", "role_of", "validate_role"]


def validate_role(gate: str, recorded_by: str) -> str:
    try:
        return _validate(gate, recorded_by)
    except FactoryError as exc:
        raise SystemExit(str(exc)) from exc
