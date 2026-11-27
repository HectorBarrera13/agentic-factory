"""Proyecto y run activos (un solo lugar en vez de un REPO_ROOT por modulo).

- `project()`: el proyecto sobre el que opera la fabrica. Por defecto, el repo que contiene
  este paquete (o `FACTORY_PROJECT`), para que la CLI siga funcionando sobre CampusFlow.
- `active_run()`: el CR cuyo `advance()` esta en curso. Las tools de los agentes leen y
  escriben codigo en SU worktree, nunca en el arbol del usuario.

Son `ContextVar`: cada hilo/tarea del servidor de la UI ve su propio proyecto y run
(usar `contextvars.copy_context().run` al lanzar hilos).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .project import Project, load_project, normalize_change_id

FACTORY_REPO = Path(__file__).resolve().parents[1]

_project: ContextVar[Project | None] = ContextVar("factory_project", default=None)
_run: ContextVar[str | None] = ContextVar("factory_active_run", default=None)
_default: Project | None = None


def default_project() -> Project:
    global _default
    if _default is None:
        _default = load_project(os.environ.get("FACTORY_PROJECT") or FACTORY_REPO)
    return _default


def project() -> Project:
    return _project.get() or default_project()


def set_project(p: Project | None):
    """Devuelve el token para `reset_project` (fixtures de tests)."""
    return _project.set(p)


def reset_project(token) -> None:
    _project.reset(token)


@contextmanager
def use_project(p: Project):
    token = _project.set(p)
    try:
        yield p
    finally:
        _project.reset(token)


def active_run() -> str | None:
    return _run.get()


@contextmanager
def run_scope(change_id: str):
    token = _run.set(normalize_change_id(change_id))
    try:
        yield
    finally:
        _run.reset(token)


def work_root() -> Path:
    """Raiz del codigo para las tools: worktree del run activo, o la raiz del proyecto."""
    return project().work_root(active_run())
