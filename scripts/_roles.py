"""Roles humanos por gate (curso §9/§10). La firma `--by "Nombre (Rol)"` debe traer el rol
autorizado para ese gate: la fábrica no acepta que cualquiera firme cualquier gate."""

from __future__ import annotations

import re

# Gate 1 (§10): "Product Owner y/o Tech Lead" aprueban alcance y plan.
GATE_ROLES: dict[str, tuple[str, ...]] = {
    "requirements": ("product owner",),
    "plan": ("tech lead", "product owner"),
    "review": ("human reviewer", "reviewer"),
    "findings": ("human reviewer", "reviewer"),
    "release": ("release manager",),
}


def role_of(recorded_by: str) -> str:
    m = re.search(r"\(([^)]+)\)\s*$", recorded_by.strip())
    return m.group(1).strip() if m else ""


def validate_role(gate: str, recorded_by: str) -> str:
    """Devuelve el rol firmante o termina con SystemExit si no corresponde al gate."""
    role = role_of(recorded_by)
    allowed = GATE_ROLES[gate]
    if not any(a in role.lower() for a in allowed):
        shown = " / ".join(a.title() for a in allowed)
        raise SystemExit(
            f"Rol no autorizado para el gate '{gate}': {role or '(sin rol)'}. "
            f'Use --by "Nombre ({shown})".'
        )
    return role
