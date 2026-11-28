"""La fabrica sobre CUALQUIER proyecto: config por proyecto, worktree por run, verificacion
por tipo de comando, servicios importables (sin API, sin tocar runs/ reales)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from factory_app import orchestrator as ORCH
from factory_app import services, tools
from factory_app.context import FACTORY_REPO, project, run_scope
from factory_app.orchestrator import AwaitingHuman, FakeExecutor
from factory_app.project import (
    Project,
    ProjectConfig,
    dumps_toml,
    load_project,
    save_config,
)
from factory_app.roles import FactoryError
from tests.factory.conftest import activate_project

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git no disponible")


# ---------------------------------------------------------------- config
def test_this_repo_config_keeps_the_course_structure():
    p = load_project(FACTORY_REPO)
    assert p.runs_dir == FACTORY_REPO / "runs"
    assert p.cr_dir == FACTORY_REPO / "change_requests"
    assert p.config.code.writable == ["campusflow", "tests/product"]
    assert [c.kind for c in p.config.verify.commands] == ["pytest", "ruff"]
    assert p.config.docs.always_include == ["NFR-01"]


def test_defaults_live_under_dot_factory(tmp_path):
    p = load_project(tmp_path)
    assert p.runs_dir == tmp_path / ".factory" / "runs"
    assert p.worktree("cr-7") == tmp_path / ".factory" / "worktrees" / "CR-7"
    assert p.branch("CR-7") == "factory/CR-7"


def test_toml_roundtrip_preserves_the_config(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORY_MODEL", raising=False)
    cfg = ProjectConfig.model_validate(
        {
            "name": "Demo",
            "code": {"source": ["app", "lib"], "tests": "spec", "language": "typescript"},
            "verify": {
                "timeout": 300,
                "commands": [
                    {"name": "tests", "cmd": "npx jest --ci", "kind": "jest"},
                    {"name": "lint", "cmd": ["npx", "eslint", "."], "kind": "lint"},
                ],
            },
            "agents": {"model": "m1", "roles": {"coder": {"model": "m2", "temperature": 0.0}}},
            "gates": {"roles": {"release": ["release manager", "tech lead"]}},
        }
    )
    project_ = Project(tmp_path)
    path = save_config(project_, cfg)
    assert tomllib.loads(path.read_text(encoding="utf-8"))  # TOML valido
    again = load_project(tmp_path).config
    assert again == cfg
    assert again.verify.commands[0].cmd == ["npx", "jest", "--ci"]
    assert again.agents.model_for("coder") == "m2"
    assert again.agents.model_for("intake") == "m1"
    assert again.agents.temperature_for("coder") == 0.0
    assert (tmp_path / ".factory" / ".gitignore").read_text().startswith("worktrees/")


def test_toml_writer_escapes_strings():
    text = dumps_toml({"name": 'say "hi"\\now', "paths": {"runs": "r"}})
    assert tomllib.loads(text) == {"name": 'say "hi"\\now', "paths": {"runs": "r"}}


@pytest.mark.parametrize(
    "data",
    [
        {"paths": {"runs": "/etc/runs"}},  # absoluta
        {"paths": {"runs": "../fuera"}},  # escape
        {"code": {"source": []}},  # sin codigo
        {"code": {"source": [".factory/x"]}},  # escribir el area de la fabrica
        {"code": {"source": ["src"], "tests": "runs"}, "paths": {"runs": "runs"}},
        {"agents": {"roles": {"hacker": {"model": "x"}}}},
        {"gates": {"roles": {"release": []}}},  # gate sin firmante
        {"git": {"branch_prefix": "factory"}},  # sin '/'
        {"verify": {"commands": [{"name": "x", "cmd": []}]}},
    ],
)
def test_invalid_configs_are_rejected(data):
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(data)


def test_env_model_overrides_the_project_model(monkeypatch):
    cfg = ProjectConfig.model_validate({"agents": {"model": "m-project"}})
    monkeypatch.delenv("FACTORY_MODEL", raising=False)
    assert cfg.agents.model_for("intake") == "m-project"
    monkeypatch.setenv("FACTORY_MODEL", "m-env")
    assert cfg.agents.model_for("intake") == "m-env"


# ---------------------------------------------------------------- verificacion por tipo
def test_jest_and_generic_commands_are_parsed_by_kind():
    v = {
        "ok": False,
        "results": [
            {"command": "npx jest", "kind": "jest",
             "stdout": "Tests:       1 failed, 9 passed, 10 total", "returncode": 1},
            {"command": "npm run build", "kind": "exit_code", "stdout": "3 passed",
             "returncode": 0},
        ],
    }
    assert ORCH.parse_verification(v) == (10, 1, "NOT_RUN")  # sin linter configurado


def test_verification_runs_configured_commands_in_the_run_worktree(tmp_path, monkeypatch):
    cfg = ProjectConfig.model_validate(
        {"verify": {"commands": [
            {"name": "py", "cmd": ["{python}", "-c", "import os; print(os.getcwd())"]},
            {"name": "missing", "cmd": ["definitely-not-a-command-xyz"]},
        ]}}
    )
    p = activate_project(tmp_path, monkeypatch, cfg)
    wt = p.worktree("CR-1")
    (wt).mkdir(parents=True)
    (wt / ".git").write_text("gitdir: x")  # basta para que cuente como worktree del run
    with run_scope("CR-1"):
        out = tools.run_baseline_verification()
    first, second = out["results"]
    assert Path(first["stdout"].strip()).resolve() == wt.resolve()
    assert first["command"].startswith("python -c")
    assert second["returncode"] is None and "no encontrado" in second["stderr"]
    assert out["ok"] is False


def test_no_verify_commands_is_a_fail_not_a_pass(tmp_path, monkeypatch):
    activate_project(tmp_path, monkeypatch, ProjectConfig())
    assert tools.run_baseline_verification()["ok"] is False


def test_integrity_guard_is_reported_not_available_for_other_languages(tmp_path, monkeypatch):
    activate_project(
        tmp_path, monkeypatch, ProjectConfig.model_validate({"code": {"language": "typescript"}})
    )
    out = ORCH.check_test_integrity({"base_commit": "abc"})
    assert out["returncode"] == 0 and "no disponible" in out["stdout"]


def test_test_analyst_naming_follows_the_language(tmp_path, monkeypatch):
    activate_project(
        tmp_path,
        monkeypatch,
        ProjectConfig.model_validate(
            {"code": {"source": ["app"], "tests": "spec", "language": "typescript"}}
        ),
    )
    assert tools.write_test_file("spec/cart.test.ts", "test('x', () => {})\n")["ok"]
    assert not tools.write_test_file("spec/helper.ts", "x").get("ok")
    assert not tools.write_test_file("app/cart.test.ts", "x").get("ok")
    assert tools.write_product_file("app/cart.ts", "export const x = 1\n")["ok"]
    assert not tools.write_product_file(".factory/config.toml", "x").get("ok")
    assert not tools.write_product_file(".env.local", "x").get("ok")


# ---------------------------------------------------------------- proyecto ajeno de punta a punta
def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture()
def ts_project(tmp_path, monkeypatch):
    """Proyecto TypeScript con rutas propias y config en .factory/config.toml."""
    if GIT is None:
        pytest.skip("git no disponible")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "cart.ts").write_text("export const total = 1\n")
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "cart.test.ts").write_text("test('AC-CART.1 total', () => {})\n")
    cfg = ProjectConfig.model_validate(
        {
            "name": "Shop",
            "code": {"source": ["app"], "tests": "spec", "language": "typescript"},
            "docs": {"requirements": None, "spec": None, "architecture": None},
            "verify": {"commands": [
                {"name": "ok", "cmd": ["{python}", "-c", "print('Tests: 3 passed, 3 total')"],
                 "kind": "jest"},
            ]},
        }
    )
    p = Project(tmp_path)
    save_config(p, cfg)
    _sh(tmp_path, "init", "-q", "-b", "trunk")
    _sh(tmp_path, "add", "-A")
    _sh(tmp_path, "commit", "-qm", "base")
    p = load_project(tmp_path)
    activate_project(tmp_path, monkeypatch, p.config)
    (p.cr_dir).mkdir(parents=True)
    (p.cr_dir / "CR-001-cart.md").write_text("# CR-001\n\n- AC-CART.1 total con descuento\n")
    return project()


class TsCoder(FakeExecutor):
    def run_agent(self, phase, context):
        if phase == "implement":
            out = tools.write_product_file("app/cart.ts", "export const total = 2\n")
            assert out["ok"], out
        return super().run_agent(phase, context)

    def run_verification(self):
        return tools.run_baseline_verification()


def test_other_project_runs_end_to_end_in_its_own_paths(ts_project):
    p = ts_project
    services.start_run(p, "CR-001")
    run = p.run_dir("CR-001")
    assert run == p.root / ".factory" / "runs" / "CR-001"
    roles = {
        "requirements": "Ana (Product Owner)", "plan": "Luis (Tech Lead)",
        "review": "Eva (Human Reviewer)", "release": "Rita (Release Manager)",
    }
    for _ in range(10):
        try:
            st = ORCH.advance("CR-001", TsCoder())
            break
        except AwaitingHuman as exc:
            services.approve_gate(p, "CR-001", exc.gate, "APPROVED", "ok", roles[exc.gate])
    assert st["status"] == "READY FOR HUMAN MERGE"
    assert st["worktree"] == ".factory/worktrees/CR-001"
    # el arbol del usuario sigue en trunk y sin cambios; la rama del run tiene el cambio
    assert _sh(p.root, "rev-parse", "--abbrev-ref", "HEAD") == "trunk"
    assert (p.root / "app" / "cart.ts").read_text() == "export const total = 1\n"
    assert _sh(p.root, "show", "factory/CR-001:app/cart.ts") == "export const total = 2"
    # worktrees/ y runs/ se ignoran solos (.factory/.gitignore); los CRs si se versionan
    assert _sh(p.root, "status", "--porcelain").splitlines() == ["?? .factory/change_requests/"]
    verification = json.loads((run / "07-verification.txt").read_text())
    assert st["verification"]["tests"] == 3
    assert any("no disponible" in r["stdout"] for r in verification["results"])
    assert "app/cart.ts" in (run / "traceability.md").read_text()
    assert ORCH.remove_worktree("CR-001") and not p.worktree("CR-001").exists()


# ---------------------------------------------------------------- servicios
def test_services_raise_factory_error_not_system_exit(ts_project):
    p = ts_project
    with pytest.raises(FactoryError, match="Expected one Change Request"):
        services.start_run(p, "CR-404")
    services.start_run(p, "CR-001")
    with pytest.raises(FactoryError, match="Run already exists"):
        services.start_run(p, "CR-001")
    with pytest.raises(FactoryError, match="no esta esperando"):
        services.approve_gate(p, "CR-001", "plan", "APPROVED", "ok", "Luis (Tech Lead)")
    with pytest.raises(FactoryError, match="Rol no autorizado"):
        services.approve_gate(p, "CR-001", "release", "APPROVED", "ok", "Luis (Tech Lead)")
    # sin avanzar, el CR aun se edita; en cuanto corre el intake queda congelado
    services.update_change_request(p, "CR-001", "# CR-001\n\n- AC-CART.1 total\n")
    run = p.run_dir("CR-001")
    assert "AC-CART.1" in (run / "00-change-request.md").read_text(encoding="utf-8")
    with pytest.raises(AwaitingHuman):
        ORCH.advance("CR-001", FakeExecutor())
    with pytest.raises(FactoryError, match="congelado"):
        services.update_change_request(p, "CR-001", "# otro")


def test_change_requests_can_be_created_and_listed(ts_project):
    p = ts_project
    path = services.create_change_request(p, "CR-002", "Export CSV", "- AC-CSV.1 exporta\n")
    assert path.name == "CR-002-export-csv.md"
    assert path.read_text(encoding="utf-8").startswith("# CR-002 — Export CSV")
    with pytest.raises(FactoryError, match="Ya existe"):
        services.create_change_request(p, "CR-002", "Otra", "")
    listed = {c["change_id"]: c for c in services.list_change_requests(p)}
    assert set(listed) == {"CR-001", "CR-002"} and not listed["CR-002"]["has_run"]


def test_gate_roles_are_configurable_per_project(tmp_path, monkeypatch):
    cfg = ProjectConfig.model_validate({"gates": {"roles": {"release": ["tech lead"]}}})
    p = activate_project(tmp_path, monkeypatch, cfg)
    (p.cr_dir).mkdir(parents=True)
    (p.cr_dir / "CR-001-x.md").write_text("# CR-001\n")
    services.start_run(p, "CR-001")
    ORCH._update_state(p.run_dir("CR-001"), awaiting_gate="release")
    with pytest.raises(FactoryError, match="Rol no autorizado"):
        services.approve_gate(p, "CR-001", "release", "APPROVED", "ok", "Rita (Release Manager)")
    assert services.approve_gate(p, "CR-001", "release", "APPROVED", "ok", "Ana (Tech Lead)")


@pytest.mark.parametrize("roles", [["cto"], ["release manager", "qa lead"], []])
def test_gate_roles_only_from_the_catalog(roles):
    with pytest.raises(ValueError):
        ProjectConfig.model_validate({"gates": {"roles": {"release": roles}}})


def test_cli_accepts_another_project(ts_project):
    r = subprocess.run(
        [sys.executable, str(FACTORY_REPO / "scripts" / "start_run.py"), "CR-001",
         "--project", str(ts_project.root)],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr
    assert (ts_project.root / ".factory" / "runs" / "CR-001" / "factory-state.json").is_file()


# ---------------------------------------------------------------- proveedores por rol
def test_old_configs_default_to_gemini_and_roles_can_switch_provider(monkeypatch):
    monkeypatch.delenv("FACTORY_MODEL", raising=False)
    old = ProjectConfig.model_validate({"agents": {"model": "gemini-2.5-flash"}})
    assert old.agents.provider_model_for("coder") == ("gemini", "gemini-2.5-flash")
    cfg = ProjectConfig.model_validate({"agents": {
        "provider": "anthropic", "model": "claude-sonnet-x",
        "roles": {"reviewer": {"provider": "openai", "model": "gpt-4.1"},
                  "security": {"model": "claude-opus-x"}},
    }})
    a = cfg.agents
    assert a.provider_model_for("coder") == ("anthropic", "claude-sonnet-x")
    assert a.provider_model_for("reviewer") == ("openai", "gpt-4.1")
    assert a.provider_model_for("security") == ("anthropic", "claude-opus-x")
    assert a.in_use() == [("anthropic", "claude-sonnet-x"), ("openai", "gpt-4.1"),
                          ("anthropic", "claude-opus-x")]


def test_factory_model_env_only_applies_to_gemini(monkeypatch):
    monkeypatch.setenv("FACTORY_MODEL", "gemini-pool-b")
    cfg = ProjectConfig.model_validate({"agents": {
        "provider": "anthropic", "model": "claude-sonnet-x",
        "roles": {"intake": {"provider": "gemini", "model": "gemini-2.5-flash"}}}})
    assert cfg.agents.model_for("coder") == "claude-sonnet-x"
    assert cfg.agents.model_for("intake") == "gemini-2.5-flash"  # override explicito gana
    gem = ProjectConfig.model_validate({"agents": {"provider": "gemini", "model": "g-a"}})
    assert gem.agents.model_for("coder") == "gemini-pool-b"


@pytest.mark.parametrize(
    "agents",
    [
        {"provider": "mistral"},
        {"model": "two words"},
        {"model": " "},
        {"roles": {"coder": {"provider": "openai"}}},  # otro proveedor sin modelo
        {"roles": {"coder": {"model": "bad name"}}},
    ],
)
def test_invalid_provider_settings_are_rejected(agents):
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate({"agents": agents})


def test_toml_roundtrip_keeps_providers(tmp_path):
    cfg = ProjectConfig.model_validate({"agents": {
        "provider": "ollama", "model": "llama3.2:3b",
        "roles": {"reviewer": {"provider": "anthropic", "model": "claude-x",
                               "temperature": 0.0}}}})
    save_config(Project(tmp_path), cfg)
    again = load_project(tmp_path).config.agents
    assert again.provider_model_for("reviewer") == ("anthropic", "claude-x")
    assert again.provider_model_for("coder") == ("ollama", "llama3.2:3b")
