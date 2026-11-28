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


def test_wizard_finds_the_real_code_folder_of_a_vanilla_js_project(client, tmp_path):
    other = tmp_path / "notas"
    for folder, name in [("js", "app.js"), ("css", "styles.css"), ("tests", "a.test.mjs"),
                         ("scripts", "x.js"), ("node_modules/lib", "index.js")]:
        (other / folder).mkdir(parents=True)
        (other / folder / name).write_text("//")
    (other / "package.json").write_text('{"name": "notas", "scripts": {"test": "node --test"}}')
    out = client.post("/api/projects/detect", json={"path": str(other)}).json()
    assert out["config"]["code"]["source"] == ["js"]
    assert not any("No se encontro la carpeta de codigo" in n for n in out["notes"])


def test_wizard_warns_when_it_cannot_find_the_code_folder(client, tmp_path):
    other = tmp_path / "vacio"
    other.mkdir()
    (other / "package.json").write_text('{"name": "vacio"}')
    out = client.post("/api/projects/detect", json={"path": str(other)}).json()
    assert out["config"]["code"]["source"] == ["src"]
    assert any("No se encontro la carpeta de codigo src" in n for n in out["notes"])


# ---------------------------------------------------------------- CRs y perfiles
def test_change_requests_crud_and_freeze(client):
    r = client.post("/api/crs", json={"change_id": "CR-002", "title": "Export", "body": "x"})
    assert r.status_code == 200
    ids = [c["change_id"] for c in client.get("/api/crs").json()["items"]]
    assert ids == ["CR-001", "CR-002"]
    assert client.put("/api/crs/CR-002", json={"body": "# CR-002\nnuevo"}).status_code == 200
    client.post("/api/runs", json={"change_id": "CR-002"})
    # run creado pero sin avanzar: el CR sigue editable y su copia de evidencia lo sigue
    cr = client.get("/api/crs/CR-002").json()
    assert cr["editable"] is True and cr["frozen"] is False and cr["deletable"] is False
    edited = {"body": "# CR-002\nantes del intake"}
    assert client.put("/api/crs/CR-002", json=edited).status_code == 200
    copy = client.get("/api/runs/CR-002/artifacts/00-change-request.md").json()["content"]
    assert "antes del intake" in copy
    client.post("/api/runs/CR-002/advance?wait=true", json={"mode": "fake"})
    cr = client.get("/api/crs/CR-002").json()
    assert cr["frozen"] is True and cr["editable"] is False and "congelado" in cr["reason"]
    r = client.put("/api/crs/CR-002", json={"body": "otro"})
    assert r.status_code == 400 and r.json()["code"] == "cr_frozen"


def test_finished_crs_are_archived_restored_and_deleted(client, project_dir):
    pid = _profile(client)
    sign = {"profile_id": pid, "role": "Product Owner"}
    client.post("/api/runs", json={"change_id": "CR-001"})
    r = client.post("/api/crs/CR-001/archive", json=sign)
    assert r.status_code == 400 and r.json()["code"] == "not_finished"
    # rechazar requirements => ESCALATED definitivo (terminado)
    client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"})
    client.post("/api/runs/CR-001/gates/requirements", json={
        "decision": "REJECTED", "reason": "no", **sign})
    client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"})
    cr = client.get("/api/crs/CR-001").json()
    assert cr["archivable"] is True and cr["deletable"] is True
    r = client.post("/api/crs/CR-001/archive", json=sign)
    assert r.status_code == 200, r.text
    assert r.json()["archived_by"] == "Ana (Product Owner)"
    assert [c["change_id"] for c in client.get("/api/crs").json()["items"]] == []
    assert client.get("/api/runs").json()["items"] == []
    archived = client.get("/api/crs?archived=1").json()["items"]
    assert [(a["change_id"], a["status"]) for a in archived] == [("CR-001", "ESCALATED")]
    assert client.post("/api/crs/CR-001/restore").status_code == 200
    assert [c["change_id"] for c in client.get("/api/crs").json()["items"]] == ["CR-001"]
    assert client.get("/api/runs").json()["items"][0]["status"] == "ESCALATED"
    # eliminar exige teclear el ID
    r = client.request("DELETE", "/api/crs/CR-001", json={"confirm": "CR-002", **sign})
    assert r.status_code == 400 and r.json()["code"] == "confirm_mismatch"
    r = client.request("DELETE", "/api/crs/CR-001", json={"confirm": "cr-001", **sign})
    assert r.status_code == 200, r.text
    assert client.get("/api/crs").json()["items"] == []
    assert client.get("/api/crs/CR-001").status_code == 400


def test_a_cr_without_a_run_can_be_deleted_but_not_archived(client):
    pid = _profile(client)
    sign = {"profile_id": pid, "role": "Product Owner"}
    client.post("/api/crs", json={"change_id": "CR-009", "title": "Algo", "body": "## Problema\nx"})
    items = {c["change_id"]: c for c in client.get("/api/crs").json()["items"]}
    assert items["CR-009"]["title"] == "CR-009 — Algo"  # "## Problema" no es el titulo
    assert client.post("/api/crs/CR-009/archive", json=sign).json()["code"] == "not_finished"
    r = client.request("DELETE", "/api/crs/CR-009", json={"confirm": "CR-009", **sign})
    assert r.status_code == 200
    r = client.request("DELETE", "/api/crs/..%2F..%2Fx", json={"confirm": "x", **sign})
    assert r.status_code in (400, 404)  # un ID con ruta nunca sale de las carpetas


def test_profiles_sign_only_with_their_own_roles(client):
    pid = _profile(client, roles=["Tech Lead"])
    client.post("/api/runs", json={"change_id": "CR-001"})
    r = client.post("/api/runs/CR-001/gates/requirements", json={
        "decision": "APPROVED", "reason": "ok", "profile_id": pid, "role": "Product Owner"})
    assert r.status_code == 400 and "no tiene el rol" in r.json()["error"]


def test_retry_signs_as_po_and_reports_the_attempt_cap(client, project_dir):
    pid = _profile(client)
    tl = _profile(client, name="Leo", roles=["Tech Lead"])
    client.post("/api/runs", json={"change_id": "CR-001"})
    body = {"body": "# CR-001\n\n- AC-V.1 value es 3\n", "reason": "alcance nuevo",
            "profile_id": pid, "role": "Product Owner"}
    r = client.post("/api/runs/CR-001/retry", json=body)
    assert r.status_code == 400 and "ESCALATED o NOT READY" in r.json()["error"]
    # rechazar el requirements gate => ESCALATED definitivo
    client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"})
    client.post("/api/runs/CR-001/gates/requirements", json={
        "decision": "REJECTED", "reason": "no", "profile_id": pid, "role": "Product Owner"})
    client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"})
    assert client.get("/api/crs/CR-001").json()["retryable"] is True
    r = client.post("/api/runs/CR-001/retry", json={**body, "profile_id": tl, "role": "Tech Lead"})
    assert r.status_code == 400  # solo firma el PO
    r = client.post("/api/runs/CR-001/retry", json=body)
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["attempt"] == 2 and st["retry"]["by"] == "Ana (Product Owner)"
    assert client.get("/api/crs/CR-001").json()["retryable"] is False
    assert "value es 3" in (project_dir / ".factory" / "change_requests" / "CR-001-value.md"
                            ).read_text(encoding="utf-8")
    # tope de intentos: la API devuelve un code para que la UI pida confirmacion
    for _ in range(3):
        client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"})
        client.post("/api/runs/CR-001/gates/requirements", json={
            "decision": "REJECTED", "reason": "no", "profile_id": pid, "role": "Product Owner"})
        client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"})
        r = client.post("/api/runs/CR-001/retry", json=body)
        if r.status_code != 200:
            break
    assert r.status_code == 400 and r.json()["code"] == "attempts_exhausted"
    assert client.post("/api/runs/CR-001/retry", json={**body, "force": True}).status_code == 200


def test_activity_endpoint_pages_and_snapshot_follows_it(client, project_dir):
    from factory_app import activity

    client.post("/api/runs", json={"change_id": "CR-001"})
    assert client.get("/api/runs/CR-001/activity").json() == {"items": [], "last": 0}
    client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"})
    out = client.get("/api/runs/CR-001/activity").json()
    kinds = [e["kind"] for e in out["items"]]
    assert kinds[:2] == ["phase_start", "phase_end"] and "gate_open" in kinds
    assert out["items"][0]["role"] == "intake" and out["last"] == len(out["items"])
    assert client.get(f"/api/runs/CR-001/activity?after={out['last']}").json()["items"] == []
    assert client.get("/api/runs/CR-999/activity").status_code == 400
    # un evento nuevo cambia el snapshot del SSE (la UI se entera sin sondear)
    p = Project(project_dir)
    run = p.run_dir("CR-001")
    before = runs_mod.snapshot(p, runs_mod.RunManager())
    activity.emit("tool_start", run, phase="plan", tool="read_repo_file", target="x")
    assert runs_mod.snapshot(p, runs_mod.RunManager()) != before
    # sin fase abierta no hay "live" (el run no esta avanzando)
    assert client.get("/api/runs/CR-001").json()["live"] is None


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
    # pragma: allowlist nextline secret
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
    # pragma: allowlist nextline secret
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
    # pragma: allowlist nextline secret
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


# ---------------------------------------------------------------- pausa / modelos en uso / arranque
def test_pause_needs_a_run_in_progress(client):
    client.post("/api/runs", json={"change_id": "CR-001"})
    r = client.post("/api/runs/CR-001/pause")
    assert r.status_code == 400 and "no esta avanzando" in r.json()["error"]


def test_pause_from_the_api_cuts_the_run_and_resume_continues(client, monkeypatch):
    import time

    from factory_app import control

    class Slow(ORCH.FakeExecutor):
        def run_agent(self, phase, context):
            for _ in range(200):  # simula una llamada larga al modelo
                if control.pause_requested():
                    raise control.Paused(f"pausado durante {phase}")
                time.sleep(0.02)
            return super().run_agent(phase, context)

    monkeypatch.setattr(runs_mod.RunManager, "_executor", lambda self, mode: Slow())
    client.post("/api/runs", json={"change_id": "CR-001"})
    assert client.post("/api/runs/CR-001/advance", json={"mode": "adk"}).json()["kind"] == "running"
    assert client.post("/api/runs/CR-001/pause").json()["pausing"] is True
    for _ in range(100):
        out = client.get("/api/runs/CR-001").json()["outcome"]
        if out["kind"] != "running":
            break
        time.sleep(0.05)
    assert out["kind"] == "paused" and out["mode"] == "adk"
    st = client.get("/api/runs/CR-001").json()["state"]
    assert st["status"] == "INTAKE_PENDING" and st["paused"]["status"] == "INTAKE_PENDING"
    listed = {r["change_id"]: r for r in client.get("/api/runs").json()["items"]}
    assert listed["CR-001"]["paused"] is True  # la lista de CRs lo muestra pausado
    monkeypatch.setattr(runs_mod.RunManager, "_executor", lambda self, mode: ORCH.FakeExecutor())
    out = client.post("/api/runs/CR-001/advance?wait=true", json={"mode": "fake"}).json()
    assert out["kind"] == "awaiting" and out["gate"] == "requirements"
    assert "paused" not in client.get("/api/runs/CR-001").json()["state"]
    assert client.get("/api/runs").json()["items"][0]["paused"] is False


def test_models_in_use_report_probe_and_env_override(client, monkeypatch):
    from factory_app import probe

    cfg = client.get("/api/config").json()["config"]
    cfg["agents"] = {"provider": "gemini", "model": "gemini-a", "temperature": 0.2,
                     "roles": {"reviewer": {"provider": "openai", "model": "gpt-x"}}}
    assert client.put("/api/config", json=cfg).status_code == 200
    probe._store("openai", "gpt-x", {"provider": "openai", "model": "gpt-x", "status": "OK",
                                     "message": "", "at": 1, "seconds": 1})
    monkeypatch.delenv("FACTORY_MODEL", raising=False)
    items = {i["model"]: i for i in client.get("/api/models/in-use").json()["items"]}
    assert items["gpt-x"]["roles"] == ["reviewer"] and items["gpt-x"]["probe"]["status"] == "OK"
    assert items["gemini-a"]["probe"] is None and items["gemini-a"]["env_override"] is False
    monkeypatch.setenv("FACTORY_MODEL", "gemini-env")
    items = {i["model"]: i for i in client.get("/api/models/in-use").json()["items"]}
    assert "gemini-a" not in items
    assert items["gemini-env"]["env_override"] is True
    assert items["gemini-env"]["configured_model"] == "gemini-a"
    assert items["gpt-x"]["env_override"] is False  # no aplica a otros proveedores


def test_new_projects_propose_the_last_model_that_passed_the_probe(client, tmp_path):
    from factory_app import probe

    other = tmp_path / "nuevo"
    other.mkdir()
    out = client.post("/api/projects/detect", json={"path": str(other)}).json()
    assert "suggested_model" not in out  # sin probes: el default del codigo
    for at, (prov, model, status) in enumerate([("ollama", "llama3", "OK"),
                                                ("openai", "gpt-x", "OK"),
                                                ("gemini", "g-roto", "ERROR")]):
        probe._store(prov, model, {"provider": prov, "model": model, "status": status,
                                   "message": "", "at": at, "seconds": 1})
    out = client.post("/api/projects/detect", json={"path": str(other)}).json()
    assert (out["config"]["agents"]["provider"], out["config"]["agents"]["model"]) == \
        ("openai", "gpt-x")
    assert out["suggested_model"] == {"provider": "openai", "model": "gpt-x"}


def test_browse_returns_the_folder_chosen_in_the_native_dialog(client, monkeypatch, tmp_path):
    from factory_ui import app as app_mod

    seen = []
    monkeypatch.setattr(app_mod, "browse_folder", lambda start: seen.append(start) or "C:/x")
    assert client.post("/api/projects/browse").json() == {"path": "C:/x"}
    assert seen and seen[0] == str(Path(seen[0]))  # parte de la carpeta del proyecto actual
    monkeypatch.setattr(app_mod, "browse_folder", lambda start: None)
    assert client.post("/api/projects/browse").json() == {"path": None}


def test_browse_folder_handles_cancel_and_missing_tkinter(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from factory_app.roles import FactoryError
    from factory_ui import app as app_mod

    def fake_run(out, code=0):
        return lambda *a, **k: SimpleNamespace(returncode=code, stdout=out, stderr="")

    monkeypatch.setattr(app_mod.subprocess, "run", fake_run(f"{tmp_path}\n"))
    assert app_mod.browse_folder(str(tmp_path)) == str(tmp_path)
    monkeypatch.setattr(app_mod.subprocess, "run", fake_run(""))
    assert app_mod.browse_folder() is None
    monkeypatch.setattr(app_mod.subprocess, "run", fake_run("", code=1))
    with pytest.raises(FactoryError, match="escribe la ruta"):
        app_mod.browse_folder()


def test_startup_opens_the_last_project_that_still_exists(tmp_path):
    from factory_ui.app import last_project

    store = Store(tmp_path / "home")
    assert last_project(store) is None
    keep, gone = tmp_path / "keep", tmp_path / "gone"
    keep.mkdir()
    gone.mkdir()
    store.add_recent(keep)
    store.add_recent(gone)
    gone.rmdir()
    assert last_project(store) == keep
