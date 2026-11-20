"""T06/E06: la escritura acotada es un rechazo de RUNTIME, no una instruccion."""

from __future__ import annotations

import pytest

from factory_app import tools


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "REPO_ROOT", tmp_path)
    (tmp_path / "campusflow").mkdir()
    (tmp_path / "tests").mkdir()
    return tmp_path


def test_coder_can_write_product_and_tests(sandbox):
    assert tools.write_product_file("campusflow/service.py", "x = 1\n")["ok"]
    assert tools.write_product_file(
        "tests/product/test_new.py", "def test_x():\n    assert True\n"
    )["ok"]


@pytest.mark.parametrize(
    "path",
    [
        "docs/REQUIREMENTS.md",  # redefinir requisitos para "cumplir": DENEGADO
        "docs/SPEC.md",
        "AGENTS.md",
        "factory_app/orchestrator.py",  # el agente no reescribe la fabrica
        "scripts/approve_gate.py",  # ni los gates
        "change_requests/CR-001.md",  # ni el input
        "runs/CR-001/07-verification.txt",  # ni la evidencia
        ".env",  # ni secretos/config
        "pyproject.toml",
        "campusflow/../../outside.py",  # escape de ruta fuera del repo
    ],
)
def test_coder_cannot_write_control_surfaces(sandbox, path):
    result = tools.write_product_file(path, "malicious")
    assert not result.get("ok"), path


def test_denial_does_not_write_anything(sandbox):
    tools.write_product_file("docs/REQUIREMENTS.md", "x")
    assert not (sandbox / "docs" / "REQUIREMENTS.md").exists()


def test_existing_product_files_readable_but_not_the_factory(sandbox):
    assert tools.read_repo_file("campusflow/service.py").__class__ is dict  # ok o error controlado
    denied = tools.read_repo_file("factory_app/orchestrator.py")
    assert denied.get("error") or not denied.get("ok")  # factory_app fuera de READABLE_ROOTS
