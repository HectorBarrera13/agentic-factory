"""Comun a los scripts: `--project RUTA` (default: este repo o FACTORY_PROJECT) y
FactoryError -> SystemExit con el mensaje para el humano."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory_app.context import default_project  # noqa: E402
from factory_app.project import Project, load_project  # noqa: E402
from factory_app.roles import FactoryError  # noqa: E402

__all__ = ["FactoryError", "pop_project", "run"]


def pop_project(argv: list[str]) -> tuple[Project, list[str]]:
    if "--project" not in argv:
        return default_project(), argv
    i = argv.index("--project")
    if i + 1 >= len(argv):
        raise SystemExit("--project requiere una ruta")
    return load_project(argv[i + 1]), argv[:i] + argv[i + 2 :]


def run(fn, *args):
    try:
        return fn(*args)
    except FactoryError as exc:
        raise SystemExit(str(exc)) from exc
