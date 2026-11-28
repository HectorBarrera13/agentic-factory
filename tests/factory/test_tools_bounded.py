"""T06/E06: la escritura acotada es un rechazo de RUNTIME, no una instruccion."""

from __future__ import annotations

import pytest

from factory_app import tools
from tests.factory.conftest import activate_project


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    activate_project(tmp_path, monkeypatch)
    (tmp_path / "campusflow").mkdir()
    (tmp_path / "tests").mkdir()
    return tmp_path


def test_coder_can_write_product_and_tests(sandbox):
    assert tools.write_product_file("campusflow/service.py", "x = 1\n")["ok"]
    assert tools.write_product_file(
        "tests/product/test_new.py", "def test_x():\n    assert True\n"
    )["ok"]


def test_in_a_phase_an_existing_file_must_be_read_before_it_is_rewritten(sandbox):
    (sandbox / "campusflow" / "app.py").write_text("ORIGINAL = 1\n", encoding="utf-8")
    with tools.phase_reads():
        out = tools.write_product_file("campusflow/app.py", "x = 1\n")
        assert out["ok"] is False and "lee el archivo antes" in out["error"]
        assert (sandbox / "campusflow" / "app.py").read_text() == "ORIGINAL = 1\n"
        assert tools.read_product_file("campusflow/app.py")["ok"]
        assert tools.write_product_file("campusflow/app.py", "ORIGINAL = 1\nx = 1\n")["ok"]
        # un archivo nuevo se crea, y el agente puede reescribir lo que el mismo creo
        assert tools.write_product_file("campusflow/new.py", "a = 1\n")["ok"]
        assert tools.write_product_file("campusflow/new.py", "a = 2\n")["ok"]
        # el Test Analyst hereda la guarda
        (sandbox / "tests" / "product").mkdir()
        (sandbox / "tests" / "product" / "test_old.py").write_text("def test_a(): pass\n")
        assert tools.write_test_file("tests/product/test_old.py", "x")["ok"] is False
    # fuera de una fase (scripts, FakeExecutor) no hay guarda
    assert tools.write_product_file("campusflow/app.py", "y = 2\n")["ok"]


def test_each_phase_starts_without_known_files(sandbox):
    (sandbox / "campusflow" / "app.py").write_text("A = 1\n", encoding="utf-8")
    with tools.phase_reads():
        tools.read_product_file("campusflow/app.py")
    with tools.phase_reads():
        assert tools.write_product_file("campusflow/app.py", "B = 1\n")["ok"] is False


def test_a_failed_phase_undoes_its_writes(sandbox):
    app = sandbox / "campusflow" / "app.py"
    app.write_text("ORIGINAL = 1\n", encoding="utf-8")
    with pytest.raises(RuntimeError), tools.phase_writes():
        assert tools.write_product_file("campusflow/app.py", "CAMBIADO = 1\n")["ok"]
        assert tools.write_product_file("campusflow/nuevo/mod.py", "a = 1\n")["ok"]
        assert tools.write_product_file("campusflow/nuevo/mod.py", "a = 2\n")["ok"]
        raise RuntimeError("429 RESOURCE_EXHAUSTED")  # la fase falla a medias
    # el worktree vuelve a su punto de control: nada a medias que bloquee el reanudar
    assert app.read_text(encoding="utf-8") == "ORIGINAL = 1\n"
    assert not (sandbox / "campusflow" / "nuevo").exists()
    assert (sandbox / "campusflow").is_dir()  # no borra carpetas que ya existian


def test_a_phase_that_succeeds_keeps_its_writes(sandbox):
    with tools.phase_writes():
        assert tools.write_product_file("campusflow/ok.py", "a = 1\n")["ok"]
    assert (sandbox / "campusflow" / "ok.py").read_text(encoding="utf-8") == "a = 1\n"


@pytest.mark.parametrize(
    "path",
    [
        "docs/REQUIREMENTS.md",  # redefinir requisitos para "cumplir": DENEGADO
        "docs/SPEC.md",
        "AGENTS.md",
        "factory_app/orchestrator.py",  # el agente no reescribe la fabrica
        "tests/factory/test_orchestrator.py",  # ni los tests DE la fabrica (solo producto)
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
    assert denied.get("error") or not denied.get("ok")  # factory_app fuera de las areas legibles
