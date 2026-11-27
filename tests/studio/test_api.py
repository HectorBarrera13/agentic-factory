"""Factory Studio (backend): seguridad local, archivos, CRs, runs y gates via API.

Sin API key (FakeExecutor) y sin tocar el repo real: cada test usa un proyecto temporal y
un FACTORY_HOME temporal para perfiles/recientes.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factory_app import orchestrator as ORCH
from factory_app.project import Project, ProjectConfig, save_config
from factory_ui import runs as runs_mod
from factory_ui.app import create_app
from factory_ui.store import Store

TOKEN = "test-token"
GIT = shutil.which("git")
CONFIG = {
    "name": "Demo",
    "code": {"source": ["app"], "tests": "tests", "language": "python"},
    "docs": {"requirements": None, "spec": None, "architecture": None},
    "verify": {"commands": [{"name": "t", "cmd": ["{python}", "-c", "print(1)"]}]},
}


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture()
def project_dir(tmp_path):
    root = tmp_path / "demo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "service.py").write_bytes(b"VALUE = 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_service.py").write_text("def test_ok():\n    assert True\n")
    (root / ".env").write_text("GOOGLE_API_KEY=secret-value\n")
    (root / ".gitignore").write_text(".env\n")
    save_config(Project(root), ProjectConfig.model_validate(CONFIG))
    if GIT:
        _sh(root, "init", "-q", "-b", "main")
        _sh(root, "add", "-A")
        _sh(root, "commit", "-qm", "base")
    cr = root / ".factory" / "change_requests"
    cr.mkdir(parents=True)
    (cr / "CR-001-value.md").write_text("# CR-001 value\n\n- AC-V.1 value es 2\n")
    return root


@pytest.fixture()
def client(project_dir, tmp_path):
    app = create_app(
        project_dir, token=TOKEN, store=Store(tmp_path / "home"),
        allowed_hosts={"testserver"},
    )
    with TestClient(app, headers={"X-Factory-Token": TOKEN}) as c:
        yield c


def _profile(client, name="Ana", roles=("Product Owner", "Tech Lead", "Human Reviewer",
                                        "Release Manager")):
    r = client.post("/api/profiles", json={"name": name, "roles": list(roles)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- seguridad
def test_api_requires_the_session_token(project_dir, tmp_path):
    app = create_app(project_dir, token=TOKEN, store=Store(tmp_path / "h"),
                     allowed_hosts={"testserver"})
    with TestClient(app) as c:
        assert c.get("/api/session").status_code == 401
        assert c.get("/api/session", headers={"X-Factory-Token": "wrong"}).status_code == 401
        # /?token= deja la cookie HttpOnly y redirige; luego la API funciona con la cookie
        r = c.get(f"/?token={TOKEN}", follow_redirects=False)
        assert r.status_code == 303 and "httponly" in r.headers["set-cookie"].lower()
        assert c.get("/api/session").status_code == 200


def test_unknown_host_is_rejected(project_dir, tmp_path):
    app = create_app(project_dir, token=TOKEN, store=Store(tmp_path / "h"),
                     allowed_hosts={"127.0.0.1:8765"})
    with TestClient(app, headers={"X-Factory-Token": TOKEN}) as c:
        assert c.get("/api/session").status_code == 403  # Host: testserver


# ---------------------------------------------------------------- archivos
def test_editor_reads_and_writes_inside_the_project(client, project_dir):
    tree = client.get("/api/files/tree").json()
    names = {e["name"] for e in tree["entries"]}
    assert {"app", "tests", ".factory"} <= names and ".git" not in names
    assert client.get("/api/files/content", params={"path": "app/service.py"}).json()[
        "content"] == "VALUE = 1\n"
    r = client.put("/api/files/content", json={"path": "app/service.py", "content": "VALUE = 3\n"})
    assert r.status_code == 200
    assert (project_dir / "app" / "service.py").read_text() == "VALUE = 3\n"


@pytest.mark.parametrize("path", ["../outside.txt", ".git/config", ".factory/worktrees/CR-1/x"])
def test_editor_cannot_escape_or_touch_git(client, path):
    r = client.put("/api/files/content", json={"path": path, "content": "x"})
    assert r.status_code == 400


def test_secrets_are_listed_but_never_served(client):
    tree = client.get("/api/files/tree").json()
    env = next(e for e in tree["entries"] if e["name"] == ".env")
    assert env["secret"] is True
    out = client.get("/api/files/content", params={"path": ".env"}).json()
    assert out["secret"] and out["content"] == "" and "secret-value" not in str(out)
    assert client.put("/api/files/content", json={"path": ".env", "content": "x"}).status_code \
        == 400


def test_secret_detection_keeps_templates_visible():
    from factory_ui.files import is_secret

    assert is_secret(".env") and is_secret(".env.local") and is_secret("server.pem")
    assert not is_secret(".env.example") and not is_secret(".env.sample")


# ---------------------------------------------------------------- config
def test_config_is_validated_before_saving(client, project_dir):
    cfg = client.get("/api/config").json()["config"]
    cfg["code"]["source"] = ["../fuera"]
    r = client.put("/api/config", json=cfg)
    assert r.status_code == 422
    cfg["code"]["source"] = ["app", "lib"]
    cfg["limits"]["verify_rounds"] = 2
    assert client.put("/api/config", json=cfg).status_code == 200
    text = (project_dir / ".factory" / "config.toml").read_text(encoding="utf-8")
    assert 'source = ["app", "lib"]' in text and "verify_rounds = 2" in text


def test_wizard_detects_a_python_project(client, tmp_path):
    other = tmp_path / "py"
    (other / "pkg").mkdir(parents=True)
    (other / "pkg" / "__init__.py").write_text("")
    (other / "tests").mkdir()
    (other / "pyproject.toml").write_text("[project]\nname = 'py'\n[tool.ruff]\n")
    out = client.post("/api/projects/detect", json={"path": str(other)}).json()
    cfg = out["config"]
    assert cfg["code"]["source"] == ["pkg"] and cfg["code"]["language"] == "python"
    assert [c["kind"] for c in cfg["verify"]["commands"]] == ["pytest", "ruff"]
    assert out["has_config"] is False and any("git" in n for n in out["notes"])


def test_wizard_detects_a_typescript_project(client, tmp_path):
    other = tmp_path / "ts"
    (other / "src").mkdir(parents=True)
    (other / "tsconfig.json").write_text("{}")
    (other / "package.json").write_text(
        '{"name": "web", "scripts": {"test": "jest", "lint": "eslint ."},'
        ' "devDependencies": {"jest": "1"}}'
    )
    cfg = client.post("/api/projects/detect", json={"path": str(other)}).json()["config"]
    assert cfg["code"]["language"] == "typescript" and cfg["name"] == "web"
    assert [c["kind"] for c in cfg["verify"]["commands"]] == ["jest", "lint"]


# ---------------------------------------------------------------- CRs y perfiles
def test_change_requests_crud_and_freeze(client):
    r = client.post("/api/crs", json={"change_id": "CR-002", "title": "Export", "body": "x"})
    assert r.status_code == 200
    ids = [c["change_id"] for c in client.get("/api/crs").json()["items"]]
    assert ids == ["CR-001", "CR-002"]
    assert client.put("/api/crs/CR-002", json={"body": "# CR-002\nnuevo"}).status_code == 200
    client.post("/api/runs", json={"change_id": "CR-002"})
    assert client.get("/api/crs/CR-002").json()["frozen"] is True
    assert client.put("/api/crs/CR-002", json={"body": "otro"}).status_code == 400


def test_profiles_sign_only_with_their_own_roles(client):
    pid = _profile(client, roles=["Tech Lead"])
    client.post("/api/runs", json={"change_id": "CR-001"})
    r = client.post("/api/runs/CR-001/gates/requirements", json={
        "decision": "APPROVED", "reason": "ok", "profile_id": pid, "role": "Product Owner"})
    assert r.status_code == 400 and "no tiene el rol" in r.json()["error"]


# ---------------------------------------------------------------- flujo completo
@pytest.mark.skipif(GIT is None, reason="git no disponible")
def test_full_flow_through_the_api_reaches_ready_for_human_merge(client, project_dir):
    pid = _profile(client)
    assert client.post("/api/runs", json={"change_id": "CR-001"}).status_code == 200
    roles = {"requirements": "Product Owner", "plan": "Tech Lead", "review": "Human Reviewer",
             "release": "Release Manager"}
    for _ in range(8):
        out = client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"}).json()
        if out["kind"] == "done":
            break
        assert out["kind"] == "awaiting", out
        gate = out["gate"]
        detail = client.get("/api/runs/CR-001").json()
        assert detail["gate"]["name"] == gate and detail["gate"]["question"]
        assert detail["gate"]["recorded"] is None  # gate abierto sin decision
        # pre-aprobar otro gate sigue prohibido tambien desde la UI
        other = "release" if gate != "release" else "plan"
        r = client.post(f"/api/runs/CR-001/gates/{other}", json={
            "decision": "APPROVED", "reason": "ok", "profile_id": pid, "role": roles[other]})
        assert r.status_code == 400
        r = client.post(f"/api/runs/CR-001/gates/{gate}", json={
            "decision": "APPROVED", "reason": "revisado", "profile_id": pid, "role": roles[gate]})
        assert r.status_code == 200, r.text
        assert r.json()["recorded_by"] == f"Ana ({roles[gate]})"
        # la UI ve la decision registrada (el gate sigue abierto hasta el siguiente advance)
        recorded = client.get("/api/runs/CR-001").json()["gate"]["recorded"]
        assert recorded["decision"] == "APPROVED"
        assert recorded["recorded_by"] == f"Ana ({roles[gate]})"
    detail = client.get("/api/runs/CR-001").json()
    assert detail["state"]["status"] == "READY FOR HUMAN MERGE"
    assert detail["worktree"] == ".factory/worktrees/CR-001"
    # el worktree se puede ver (solo lectura) pero no editar
    tree = client.get("/api/files/tree", params={"root": "run:CR-001"}).json()
    assert tree["readonly"] is True
    r = client.put("/api/files/content?root=run:CR-001",
                   json={"path": "app/service.py", "content": "x"})
    assert r.status_code == 400
    assert client.get("/api/runs/CR-001/diff").json()["branch"] == "factory/CR-001"
    assert client.post("/api/runs/CR-001/export").status_code == 200
    assert client.post("/api/runs/CR-001/cleanup").json()["removed"] is True
    assert _sh(project_dir, "rev-parse", "--abbrev-ref", "HEAD") == "main"


@pytest.mark.skipif(GIT is None, reason="git no disponible")
def test_review_gate_needs_every_finding_classified(client, monkeypatch):
    review = (
        "# Review\n\nMUST FIX: 1\nSHOULD FIX: 1\nRF-01 | MUST FIX | x\nRF-02 | SHOULD FIX | y\n"
    )
    monkeypatch.setattr(
        runs_mod.RunManager, "_executor", lambda self, mode: ORCH.FakeExecutor(review_report=review)
    )
    pid = _profile(client)
    client.post("/api/runs", json={"change_id": "CR-001"})
    roles = {"requirements": "Product Owner", "plan": "Tech Lead"}
    for _ in range(3):
        out = client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"}).json()
        if out.get("gate") == "review":
            break
        client.post(f"/api/runs/CR-001/gates/{out['gate']}", json={
            "decision": "APPROVED", "reason": "ok", "profile_id": pid, "role": roles[out["gate"]]})
    findings = client.get("/api/runs/CR-001").json()["findings"]
    assert {f["id"] for f in findings["items"]} == {"RF-01", "RF-02"}
    assert next(f for f in findings["items"] if f["id"] == "RF-01")["blocking"] is True
    body = {"decision": "APPROVED", "reason": "ok", "profile_id": pid, "role": "Human Reviewer"}
    r = client.post("/api/runs/CR-001/gates/review", json=body)
    assert r.status_code == 400 and "sin clasificar" in r.json()["error"]
    r = client.post("/api/runs/CR-001/findings", json={
        "verdicts": {"RF-01": "ACCEPTED"}, "profile_id": pid, "role": "Human Reviewer"})
    assert r.json()["pending"] == ["RF-02"]
    client.post("/api/runs/CR-001/findings", json={
        "verdicts": {"RF-02": "DEFERRED"}, "profile_id": pid, "role": "Human Reviewer"})
    assert client.post("/api/runs/CR-001/gates/review", json=body).status_code == 200


def test_advance_reports_errors_without_crashing(client):
    r = client.post("/api/runs/CR-404/advance?wait=true", json={"mode": "fake"})
    assert r.status_code == 400 and "Run not found" in r.json()["error"]


def test_events_stream_reports_changes(client):
    with client.stream("GET", "/api/events?max_events=1") as r:
        body = "".join(r.iter_text())
    assert "event: change" in body and '"version"' in body
    assert '"probes"' in body


# ---------------------------------------------------------------- roles del catalogo
def test_profiles_only_accept_catalog_roles(client):
    r = client.post("/api/profiles", json={"name": "Ana", "roles": ["CTO"]})
    assert r.status_code == 422
    meta = client.get("/api/meta").json()
    assert meta["role_catalog"] == ["product owner", "tech lead", "human reviewer", "reviewer",
                                    "release manager"]
    assert {p["id"] for p in meta["providers"]} == {"gemini", "anthropic", "openai", "ollama",
                                                    "openai_compatible"}


def test_old_profiles_with_unknown_roles_load_as_incomplete(client, tmp_path):
    import json as _json

    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "profiles.json").write_text(_json.dumps([
        {"id": "p1", "name": "Ana", "roles": ["CTO", "Tech Lead"]}]))
    items = client.get("/api/profiles").json()["items"]
    assert items == [{"id": "p1", "name": "Ana", "roles": ["tech lead"], "incomplete": True}]


# ---------------------------------------------------------------- proveedores
@pytest.fixture()
def mock_http():
    import httpx

    from factory_app import providers

    def install(handler):
        providers.transport = httpx.MockTransport(handler)

    yield install
    providers.transport = None


def test_providers_never_return_the_key(client, mock_http, monkeypatch):
    import httpx

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mock_http(lambda r: httpx.Response(200, json={"data": [{"id": "gpt-4.1"}]}))
    r = client.put("/api/providers/openai", json={"api_key": "sk-test-SECRET1234"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["test"] == {"ok": True, "kind": "OK", "message": "1 modelos", "models": 1}
    assert body["status"]["last4"] == "1234" and body["status"]["key_source"] == "file"
    listing = client.get("/api/providers").text
    assert "SECRET" not in listing and "SECRET" not in r.text
    models = client.get("/api/providers/openai/models").json()
    assert models == {"items": [{"id": "gpt-4.1", "label": "gpt-4.1", "created": None}],
                      "error": None}
    assert client.delete("/api/providers/openai/key").json()["status"]["has_key"] is False


def test_invalid_key_is_reported_and_models_endpoint_does_not_crash(client, mock_http,
                                                                     monkeypatch):
    import httpx

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    mock_http(lambda r: httpx.Response(401, json={"error": "bad"}))
    body = client.put("/api/providers/anthropic", json={"api_key": "sk-ant-wrong000"}).json()
    assert body["test"]["ok"] is False and body["test"]["kind"] == "INVALID_KEY"
    models = client.get("/api/providers/anthropic/models?refresh=true").json()
    assert models["items"] == [] and models["error"]["kind"] == "INVALID_KEY"


def test_env_keys_are_read_only_from_the_ui(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFROMENV12345")
    gem = next(p for p in client.get("/api/providers").json()["items"] if p["id"] == "gemini")
    assert gem["status"]["key_source"] == "env" and "FROMENV" not in str(gem)
    r = client.delete("/api/providers/gemini/key")
    assert r.status_code == 400 and "variable de entorno" in r.json()["error"]
    r = client.put("/api/providers/gemini", json={"api_key": "AIzaOTHER0000"})
    assert r.status_code == 400


def test_bad_urls_and_unknown_providers_are_rejected(client):
    assert client.put("/api/providers/ollama", json={"url": "ftp://x"}).status_code == 400
    assert client.post("/api/providers/mistral/test").status_code == 400


def test_probe_endpoint_and_probes_listing(client, monkeypatch):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from factory_app import models

    class ToolLlm(BaseLlm):
        model: str = "stub"

        async def generate_content_async(self, llm_request, stream=False):  # noqa: ARG002
            call = types.FunctionCall(name="ping", args={})
            yield LlmResponse(content=types.Content(role="model",
                                                    parts=[types.Part(function_call=call)]))

    monkeypatch.setattr(models, "build_llm", lambda p, m: ToolLlm())
    r = client.post("/api/models/probe", json={"provider": "ollama", "model": "llama3"})
    assert r.status_code == 200 and r.json()["status"] == "OK"
    listing = client.get("/api/models/probes").json()
    assert listing["items"]["ollama:llama3"]["status"] == "OK" and listing["running"] == []


def test_saving_config_probes_the_models_in_use(client, monkeypatch):
    from factory_app import probe

    launched = []
    monkeypatch.setattr(probe, "probe_in_background",
                        lambda pairs, max_age=3600: launched.append(pairs) or [])
    cfg = client.get("/api/config").json()["config"]
    cfg["agents"] = {"provider": "ollama", "model": "llama3", "temperature": 0.2,
                     "roles": {"reviewer": {"provider": "openai", "model": "gpt-4.1"}}}
    r = client.put("/api/config", json=cfg)
    assert r.status_code == 200, r.text
    assert launched == [[("ollama", "llama3"), ("openai", "gpt-4.1")]]
