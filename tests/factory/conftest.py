"""Sandbox de proyecto para los tests de la fabrica (los escaneres externos se stubean en
tests/conftest.py para toda la suite)."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory_app import context
from factory_app.project import Project, ProjectConfig, load_project

# Misma configuracion que este repo (CampusFlow): campusflow/ + tests/product, runs/ ...
REPO_CONFIG: ProjectConfig = load_project(context.FACTORY_REPO).config


def activate_project(root: Path, monkeypatch, config: ProjectConfig | None = None) -> Project:
    """El proyecto activo de la fabrica pasa a ser `root` (sandbox temporal)."""
    project = Project(root, config or REPO_CONFIG)
    monkeypatch.setattr(context, "_default", project)
    return project


@pytest.fixture()
def project_at(monkeypatch):
    return lambda root, config=None: activate_project(root, monkeypatch, config)
