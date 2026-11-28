"""Ciclo de vida del Change Request: editar hasta el intake, archivar, restaurar, eliminar.

Solo FakeExecutor y repos temporales: no requiere API key ni toca runs/ reales.
"""

from __future__ import annotations

import subprocess

import pytest

from factory_app import services
from factory_app.context import project
from factory_app.orchestrator import FakeExecutor
from factory_app.roles import FactoryError
from tests.factory.test_orchestrator import (
    CR_CLEAN,
    _git_repo,
    drive,
    sandbox,  # noqa: F401  (fixture)
)


def _cr(p, text=CR_CLEAN):
    p.cr_dir.mkdir(parents=True, exist_ok=True)
    (p.cr_dir / "CR-T-x.md").write_text(text, encoding="utf-8")


def _branch_exists(name: str) -> bool:
    r = subprocess.run(["git", "branch", "--list", name], cwd=project().root,
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def test_untouched_run_is_editable_and_its_copy_follows(sandbox):  # noqa: F811
    run = sandbox()
    p = project()
    _cr(p)
    life = services.cr_lifecycle(p, "CR-T")
    assert life == {"editable": True, "archivable": False, "deletable": False, "reason": ""}
    services.update_change_request(p, "CR-T", CR_CLEAN + "\n- nota del PO\n")
    assert "nota del PO" in (run / "00-change-request.md").read_text(encoding="utf-8")
    assert not services.cr_lifecycle(p, "CR-T", busy=True)["editable"]  # avanzando: no
    drive(run, FakeExecutor(), stop_at="requirements")
    assert not services.cr_lifecycle(p, "CR-T")["editable"]
    with pytest.raises(FactoryError) as exc:
        services.update_change_request(p, "CR-T", "# otro")
    assert exc.value.code == "cr_frozen"


def test_unfinished_runs_cannot_be_archived_or_deleted(sandbox):  # noqa: F811
    run = sandbox()
    p = project()
    _cr(p)
    drive(run, FakeExecutor(), stop_at="plan")
    for action in (lambda: services.archive_change_request(p, "CR-T", "Ana (Product Owner)"),
                   lambda: services.delete_change_request(p, "CR-T", "CR-T")):
        with pytest.raises(FactoryError) as exc:
            action()
        assert exc.value.code == "not_finished"


def test_archive_restore_and_delete_keep_the_branch(sandbox):  # noqa: F811
    run = sandbox()
    _git_repo(run.parents[1])  # repo git real: rama factory/<CR> y worktree del run
    p = project()
    _cr(p)
    state = drive(run, FakeExecutor())
    assert state["status"] == "READY FOR HUMAN MERGE"
    branch = state["branch"]
    assert p.worktree("CR-T").exists() and _branch_exists(branch)
    with pytest.raises(FactoryError) as busy:
        services.archive_change_request(p, "CR-T", "Ana (Product Owner)", busy=True)
    assert busy.value.code == "busy"

    record = services.archive_change_request(p, "CR-T", "Ana (Product Owner)")
    assert record["status"] == "READY FOR HUMAN MERGE" and record["worktree_removed"]
    assert not run.exists() and not p.worktree("CR-T").exists() and _branch_exists(branch)
    assert services.list_change_requests(p) == [] and services.list_runs(p) == []
    assert [a["change_id"] for a in services.list_archived(p)] == ["CR-T"]

    services.restore_change_request(p, "CR-T")
    assert (run / "factory-state.json").is_file()
    assert not (run / services.ARCHIVE_RECORD).exists()
    assert [c["change_id"] for c in services.list_change_requests(p)] == ["CR-T"]

    with pytest.raises(FactoryError) as wrong:
        services.delete_change_request(p, "CR-T", "CR-X")
    assert wrong.value.code == "confirm_mismatch"
    out = services.delete_change_request(p, "CR-T", "cr-t")
    assert not run.exists() and not list(p.cr_dir.glob("CR-T*.md"))
    assert any(d.endswith("CR-T-x.md") for d in out["deleted"])
    assert _branch_exists(branch)  # la rama nunca se borra


def test_an_archived_cr_can_be_deleted_and_restore_never_overwrites(sandbox):  # noqa: F811
    run = sandbox()
    p = project()
    _cr(p)
    drive(run, FakeExecutor(), decisions={"plan": "REJECTED"})
    services.archive_change_request(p, "CR-T", "Ana (Product Owner)")
    _cr(p, CR_CLEAN.replace("CR-T", "CR-T"))  # otro CR-T activo con el mismo ID
    with pytest.raises(FactoryError) as exc:
        services.restore_change_request(p, "CR-T")
    assert exc.value.code == "exists"
    (p.cr_dir / "CR-T-x.md").unlink()
    services.delete_change_request(p, "CR-T", "CR-T")
    assert services.list_archived(p) == []


def test_an_old_cr_without_h1_is_titled_by_its_file_name(sandbox):  # noqa: F811
    sandbox()
    p = project()
    p.cr_dir.mkdir(parents=True, exist_ok=True)
    (p.cr_dir / "CR-04-notas-online.md").write_text(
        "## Problema\r\nNo hay notas colaborativas\r\n## Alcance\r\n- Notas\r\n",
        encoding="utf-8",
    )
    titles = {c["change_id"]: c["title"] for c in services.list_change_requests(p)}
    assert titles["CR-04"] == "Notas online"  # no "Problema"


def test_a_cr_without_a_run_is_deletable_and_gets_its_h1(sandbox):  # noqa: F811
    sandbox()  # proyecto activo; CR-N no tiene run
    p = project()
    path = services.create_change_request(p, "CR-N", "Notas", "## Problema\nalgo\n")
    assert path.read_text(encoding="utf-8").startswith("# CR-N — Notas")
    assert [c["title"] for c in services.list_change_requests(p)] == ["CR-N — Notas"]
    life = services.cr_lifecycle(p, "CR-N")
    assert life["editable"] and life["deletable"] and not life["archivable"]
    services.delete_change_request(p, "CR-N", "CR-N")
    assert not path.exists()
