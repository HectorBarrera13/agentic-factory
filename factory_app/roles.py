"""Roles humanos por gate (curso §9/§10). La firma "Nombre (Rol)" debe traer un rol
autorizado para ese gate: la fabrica no acepta que cualquiera firme cualquier gate.
Los roles por gate son configurables por proyecto (`[gates.roles]`)."""

from __future__ import annotations

import re

from .project import DEFAULT_GATE_ROLES


class FactoryError(Exception):
    """Error de uso de la fabrica (mensaje para el humano; la CLI lo muestra y sale).

    `code` (opcional) permite a la UI reaccionar a un caso concreto sin parsear el texto."""

    def __init__(self, message: str = "", code: str = ""):
        super().__init__(message)
        self.code = code


def role_of(recorded_by: str) -> str:
    m = re.search(r"\(([^)]+)\)\s*$", str(recorded_by).strip())
    return m.group(1).strip() if m else ""


def validate_role(gate: str, recorded_by: str, roles: dict[str, list[str]] | None = None) -> str:
    """Devuelve el rol firmante o lanza FactoryError si no corresponde al gate."""
    table = roles or DEFAULT_GATE_ROLES
    if gate not in table:
        raise FactoryError(f"gate desconocido: {gate!r}")
    role = role_of(recorded_by)
    allowed = table[gate]
    if not any(a in role.lower() for a in allowed):
        shown = " / ".join(a.title() for a in allowed)
        raise FactoryError(
            f"Rol no autorizado para el gate '{gate}': {role or '(sin rol)'}. "
            f'Firme como "Nombre ({shown})".'
        )
    return role
