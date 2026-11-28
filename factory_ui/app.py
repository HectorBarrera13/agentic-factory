"""Servidor de Factory Studio.

Seguridad (servidor local):
- Solo escucha en 127.0.0.1 y valida el header Host (anti DNS-rebinding).
- Token aleatorio por sesion: `/?token=...` lo deja en una cookie HttpOnly SameSite=Strict;
  toda `/api/*` exige esa cookie o el header `X-Factory-Token`. Sin CORS.
- Archivos siempre dentro del proyecto abierto (ver files.py).

Uso: `uv run python -m factory_ui [RUTA_PROYECTO] [--port 8765] [--no-browser]`
(desarrollo del frontend: `--token dev` y `npm run dev` en ui/, que manda el header).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Literal

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from factory_app import credentials, providers, services
from factory_app import probe as probe_mod
from factory_app.orchestrator import remove_worktree
from factory_app.project import (
    GATES,
    LANGUAGES,
    ROLE_CATALOG,
    ROLES,
    VERIFY_KINDS,
    Project,
    ProjectConfig,
    load_project,
    save_config,
)
from factory_app.roles import FactoryError

from . import detect as detect_mod
from . import files as files_mod
from . import runs as runs_mod
from .store import Profile, Store

STATIC_DIR = Path(__file__).resolve().parent / "static"
COOKIE = "factory_token"
PHASES = [
    {"id": "intake", "label": "Intake", "artifact": "01-intake-report.md"},
    {"id": "requirements", "label": "Gate requisitos", "gate": "requirements"},
    {"id": "plan", "label": "Plan", "artifact": "03-implementation-plan.md"},
    {"id": "plan_gate", "label": "Gate plan", "gate": "plan"},
    {"id": "implement", "label": "Implementación", "artifact": "05-coder-report.md"},
    {"id": "verify", "label": "Verificación", "artifact": "07-verification.txt"},
    {"id": "review", "label": "Review + Security", "artifact": "08-review-report.md"},
    {"id": "review_gate", "label": "Gate review", "gate": "review"},
    {"id": "evidence", "label": "Evidencia", "artifact": "14-evidence-summary.md"},
    {"id": "release", "label": "Gate release", "gate": "release"},
]


class Session:
    def __init__(self, store: Store, project: Project | None):
        self.store = store
        self.project = project
        self.runs = runs_mod.RunManager()

    def require(self) -> Project:
        if self.project is None:
            raise FactoryError("No hay proyecto abierto")
        return self.project

    def open(self, path: str | Path) -> Project:
        root = Path(path).expanduser()
        if not root.is_dir():
            raise FactoryError(f"La carpeta no existe: {root}")
        try:
            self.project = load_project(root)
        except (ValidationError, ValueError) as exc:
            raise FactoryError(f".factory/config.toml invalido: {exc}") from exc
        self.store.add_recent(self.project.root)
        return self.project


# ---------------------------------------------------------------- modelos de entrada
class OpenBody(BaseModel):
    path: str


class FileBody(BaseModel):
    path: str
    content: str


class CrBody(BaseModel):
    change_id: str
    title: str
    body: str = ""


class CrUpdate(BaseModel):
    body: str


class SignBody(BaseModel):
    profile_id: str
    role: str


class DeleteCrBody(SignBody):
    confirm: str


class StartBody(BaseModel):
    change_id: str


class AdvanceBody(BaseModel):
    mode: Literal["fake", "adk"] = "fake"


class GateBody(BaseModel):
    decision: str
    reason: str
    profile_id: str
    role: str


class RetryBody(BaseModel):
    body: str
    reason: str
    profile_id: str
    role: str
    force: bool = False


class FindingsBody(BaseModel):
    verdicts: dict[str, str]
    profile_id: str
    role: str


class ProfileBody(BaseModel):
    name: str
    roles: list[str]


class ProviderBody(BaseModel):
    api_key: str | None = None
    url: str | None = None


class ProbeBody(BaseModel):
    provider: str
    model: str


def _provider_list() -> list[dict]:
    """Catalogo + estado de cada proveedor. Nunca incluye la clave."""
    out = []
    for pid, info in providers.PROVIDERS.items():
        out.append({
            "id": pid,
            "label": info.label,
            "description": info.description,
            "needs_key": info.needs_key,
            "needs_url": info.needs_url,
            "key_optional": not info.needs_key and bool(credentials.ENV_VARS[pid][0]),
            "temperature_range": list(info.temperature_range),
            "default_url": credentials.DEFAULT_URLS.get(pid),
            "status": credentials.status(pid),
        })
    return out


# Dialogo nativo de carpeta en un proceso aparte: tkinter necesita su propio hilo
# principal y no debe bloquear el servidor. Imprime la ruta elegida (vacio = cancelado).
BROWSE_SCRIPT = """
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askdirectory(parent=root, initialdir=sys.argv[1] or None,
                               title="Elige la carpeta del proyecto", mustexist=True)
root.destroy()
sys.stdout.write(path or "")
"""
BROWSE_TIMEOUT = 600


def browse_folder(initial: str = "") -> str | None:
    """Abre el explorador del sistema y devuelve la carpeta elegida (None si se cancela)."""
    try:
        r = subprocess.run([sys.executable, "-c", BROWSE_SCRIPT, initial], capture_output=True,
                           text=True, timeout=BROWSE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise FactoryError("el explorador quedo abierto demasiado tiempo; vuelve a intentarlo") \
            from exc
    if r.returncode != 0:
        raise FactoryError("no se pudo abrir el explorador del sistema (tkinter no disponible); "
                           "escribe la ruta a mano")
    path = r.stdout.strip()
    return str(Path(path)) if path else None


def _models_in_use(project: Project) -> list[dict]:
    """Modelos que usaran los agentes (con FACTORY_MODEL aplicado) y su ultimo probe."""
    agents = project.config.agents
    env_model = os.getenv("FACTORY_MODEL")
    out: dict[tuple[str, str], dict] = {}
    for role in ROLES:
        provider, model = agents.provider_model_for(role)
        own = (agents.roles.get(role) or None)
        override = bool(provider == "gemini" and env_model and not (own and own.model)
                        and env_model != agents.model)
        item = out.setdefault((provider, model), {
            "provider": provider, "model": model, "roles": [],
            "probe": probe_mod.result(provider, model), "env_override": False,
            "configured_model": agents.model if override else None,
        })
        item["roles"].append(role)
        item["env_override"] = item["env_override"] or override
    return list(out.values())


def _probes() -> dict:
    return {"items": probe_mod.results(), "running": sorted(probe_mod.running),
            "version": probe_mod.version}


def _project_info(session: Session) -> dict | None:
    p = session.project
    if p is None:
        return None
    return {
        "root": str(p.root),
        "name": p.name,
        "configured": p.config_path.is_file(),
        "git": (p.root / ".git").exists(),
        "config": p.config.model_dump(),
    }


def _signature(session: Session, profile_id: str, role: str) -> str:
    profile = session.store.profile(profile_id)
    if profile is None:
        raise FactoryError("Perfil no encontrado: crea o elige un perfil para firmar")
    role = role.strip().lower()
    if role not in profile.roles:
        raise FactoryError(f"El perfil {profile.name} no tiene el rol {role!r}")
    return profile.signature(role)


def _retryable(project, change_id: str) -> bool:
    try:
        return services.read_state(project, change_id).get("status") in services.RETRYABLE
    except FactoryError:
        return False


def create_app(
    project_root: str | Path | None = None,
    token: str | None = None,
    store: Store | None = None,
    allowed_hosts: set[str] | None = None,
) -> FastAPI:
    store = store or Store()
    session = Session(store, None)
    if project_root is not None:
        session.open(project_root)
    token = token or secrets.token_urlsafe(24)
    app = FastAPI(title="Factory Studio", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.session = session
    app.state.token = token
    hosts = allowed_hosts

    @app.exception_handler(FactoryError)
    async def _factory_error(_request: Request, exc: FactoryError):
        payload = {"error": str(exc), **({"code": exc.code} if exc.code else {})}
        return JSONResponse(payload, status_code=400)

    @app.exception_handler(ValidationError)
    async def _validation_error(_request: Request, exc: ValidationError):
        return JSONResponse({"error": "configuracion invalida", "details": exc.errors(
            include_url=False, include_context=False)}, status_code=422)

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        host = (request.headers.get("host") or "").lower()
        if hosts is not None and host not in hosts:
            return JSONResponse({"error": "host no permitido"}, status_code=403)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-factory-token") or request.cookies.get(COOKIE)
            if not supplied or not secrets.compare_digest(supplied, token):
                return JSONResponse({"error": "token invalido"}, status_code=401)
        return await call_next(request)

    # ------------------------------------------------------------ sesion / proyectos
    @app.get("/api/session")
    def get_session():
        return {
            "project": _project_info(session),
            "recent": store.recent(),
            "profiles": [p.model_dump() for p in store.profiles()],
        }

    @app.get("/api/meta")
    def meta():
        return {
            "phases": PHASES,
            "gates": list(services.VALID_GATES),
            "decisions": list(services.VALID_DECISIONS),
            "verdicts": list(services.VERDICTS),
            "agent_roles": list(ROLES),
            "gate_keys": list(GATES),
            "verify_kinds": list(VERIFY_KINDS),
            "languages": list(LANGUAGES),
            "role_catalog": list(ROLE_CATALOG),
            "providers": [
                {"id": pid, "label": info.label,
                 "temperature_range": list(info.temperature_range)}
                for pid, info in providers.PROVIDERS.items()
            ],
            "defaults": ProjectConfig().model_dump(),
        }

    @app.post("/api/projects/open")
    def open_project(body: OpenBody):
        session.open(body.path)
        return _project_info(session)

    @app.post("/api/projects/detect")
    def detect(body: OpenBody):
        try:
            found = detect_mod.detect(Path(body.path).expanduser())
        except FileNotFoundError as exc:
            raise FactoryError(f"La carpeta no existe: {body.path}") from exc
        suggested = probe_mod.latest_ok()
        # proyecto nuevo: proponer el ultimo modelo que paso la prueba (no el del codigo)
        if suggested and not found.get("has_config"):
            found["config"]["agents"]["provider"], found["config"]["agents"]["model"] = suggested
            found["suggested_model"] = {"provider": suggested[0], "model": suggested[1]}
        return found

    @app.post("/api/projects/browse")
    async def browse():
        start = str(session.project.root.parent) if session.project else str(Path.home())
        return {"path": await asyncio.to_thread(browse_folder, start)}

    @app.get("/api/config")
    def get_config():
        return _project_info(session) or {}

    @app.put("/api/config")
    def put_config(data: dict = Body(...)):  # noqa: B008
        p = session.require()
        config = ProjectConfig.model_validate(data)
        save_config(p, config)
        # probe automatico de los modelos en uso (sin bloquear el guardado)
        probe_mod.probe_in_background(config.agents.in_use())
        return _project_info(session)

    # ------------------------------------------------------------ proveedores / modelos
    @app.get("/api/providers")
    def list_providers():
        return {"items": _provider_list()}

    @app.put("/api/providers/{provider_id}")
    def save_provider(provider_id: str, body: ProviderBody):
        providers.info(provider_id)
        credentials.save(provider_id, api_key=body.api_key, url=body.url)
        providers.clear_cache(provider_id)
        return {"status": credentials.status(provider_id),
                "test": providers.test_connection(provider_id)}

    @app.delete("/api/providers/{provider_id}/key")
    def delete_provider_key(provider_id: str):
        providers.info(provider_id)
        status = credentials.delete_key(provider_id)
        providers.clear_cache(provider_id)
        return {"status": status}

    @app.post("/api/providers/{provider_id}/test")
    def test_provider(provider_id: str):
        return providers.test_connection(provider_id)

    @app.get("/api/providers/{provider_id}/models")
    def provider_models(provider_id: str, refresh: bool = False):
        try:
            return {"items": providers.list_models(provider_id, refresh=refresh), "error": None}
        except providers.ConnectionProblem as exc:
            return {"items": [], "error": {"kind": exc.kind, "message": str(exc)}}

    @app.get("/api/models/in-use")
    def models_in_use():
        return {"items": _models_in_use(session.require())}

    @app.get("/api/models/probes")
    def model_probes():
        return _probes()

    @app.post("/api/models/probe")
    async def run_probe(body: ProbeBody):
        providers.info(body.provider)
        if not body.model.strip():
            raise FactoryError("falta el modelo")
        # en un hilo: el probe usa asyncio.run y puede tardar
        return await asyncio.to_thread(probe_mod.probe, body.provider, body.model.strip())

    # ------------------------------------------------------------ archivos / git
    @app.get("/api/files/tree")
    def file_tree(root: str = "user"):
        return files_mod.tree(session.require(), root)

    @app.get("/api/files/content")
    def file_content(path: str, root: str = "user"):
        return files_mod.read(session.require(), root, path)

    @app.put("/api/files/content")
    def save_file(body: FileBody, root: str = "user"):
        return files_mod.write(session.require(), root, body.path, body.content)

    @app.get("/api/git/status")
    def git_status():
        return {"files": files_mod.git_status(session.require())}

    # ------------------------------------------------------------ change requests
    @app.get("/api/crs")
    def list_crs(archived: bool = False):
        p = session.require()
        if archived:
            return {"items": services.list_archived(p)}
        return {"items": services.list_change_requests(p)}

    @app.get("/api/crs/{change_id}")
    def get_cr(change_id: str):
        p = session.require()
        path = services.find_change_request(p, change_id)
        life = services.cr_lifecycle(p, change_id, busy=session.runs.busy(p, change_id))
        return {
            "change_id": change_id.upper(),
            "file": path.relative_to(p.root).as_posix(),
            "body": path.read_text(encoding="utf-8"),
            # compatibilidad: congelado = ya no se edita (el intake corrio o esta corriendo)
            "frozen": not life["editable"],
            "retryable": _retryable(p, change_id),
            **life,
        }

    @app.post("/api/crs")
    def create_cr(body: CrBody):
        p = session.require()
        path = services.create_change_request(p, body.change_id, body.title, body.body)
        return {"file": path.relative_to(p.root).as_posix()}

    @app.put("/api/crs/{change_id}")
    def update_cr(change_id: str, body: CrUpdate):
        p = session.require()
        path = services.update_change_request(
            p, change_id, body.body, busy=session.runs.busy(p, change_id))
        return {"file": path.relative_to(p.root).as_posix()}

    @app.post("/api/crs/{change_id}/archive")
    def archive_cr(change_id: str, body: SignBody):
        p = session.require()
        signed = _signature(session, body.profile_id, body.role)
        return services.archive_change_request(
            p, change_id, signed, busy=session.runs.busy(p, change_id))

    @app.post("/api/crs/{change_id}/restore")
    def restore_cr(change_id: str):
        return services.restore_change_request(session.require(), change_id)

    @app.delete("/api/crs/{change_id}")
    def delete_cr(change_id: str, body: DeleteCrBody):
        p = session.require()
        _signature(session, body.profile_id, body.role)  # solo un humano identificado borra
        return services.delete_change_request(
            p, change_id, body.confirm, busy=session.runs.busy(p, change_id))

    # ------------------------------------------------------------ runs
    @app.get("/api/runs")
    def list_runs():
        p = session.require()
        items = services.list_runs(p)
        for item in items:
            item["busy"] = session.runs.busy(p, item["change_id"])
        return {"items": items}

    @app.post("/api/runs")
    def start_run(body: StartBody):
        return services.start_run(session.require(), body.change_id)

    @app.get("/api/runs/{change_id}")
    def run_detail(change_id: str):
        return runs_mod.detail(session.require(), session.runs, change_id)

    @app.get("/api/runs/{change_id}/activity")
    def run_activity(change_id: str, after: int = 0):
        from factory_app import activity

        p = session.require()
        services.read_state(p, change_id)  # FactoryError si no existe
        return activity.read(p.run_dir(change_id), after=after)

    @app.get("/api/runs/{change_id}/artifacts/{name}")
    def run_artifact(change_id: str, name: str):
        return {"name": name, "content": runs_mod.read_artifact(session.require(), change_id, name)}

    @app.post("/api/runs/{change_id}/advance")
    def advance(change_id: str, body: AdvanceBody, wait: bool = False):
        return session.runs.advance(session.require(), change_id, body.mode, wait=wait)

    @app.post("/api/runs/{change_id}/pause")
    def pause_run(change_id: str):
        return session.runs.pause(session.require(), change_id)

    @app.post("/api/runs/{change_id}/retry")
    def retry_run(change_id: str, body: RetryBody):
        p = session.require()
        if session.runs.busy(p, change_id):
            raise FactoryError("el run esta avanzando; espera a que se detenga")
        signed = _signature(session, body.profile_id, body.role)
        return services.retry_run(p, change_id, body.body, body.reason, signed, body.force)

    @app.post("/api/runs/{change_id}/gates/{gate}")
    def decide_gate(change_id: str, gate: str, body: GateBody):
        p = session.require()
        signed = _signature(session, body.profile_id, body.role)
        return services.approve_gate(p, change_id, gate, body.decision, body.reason, signed)

    @app.post("/api/runs/{change_id}/findings")
    def decide_findings(change_id: str, body: FindingsBody):
        p = session.require()
        signed = _signature(session, body.profile_id, body.role)
        return services.decide_findings(p, change_id, body.verdicts, signed)

    @app.get("/api/runs/{change_id}/diff")
    def run_diff(change_id: str):
        return runs_mod.diff_files(session.require(), change_id)

    @app.get("/api/runs/{change_id}/diff/file")
    def run_diff_file(change_id: str, path: str):
        return runs_mod.diff_file(session.require(), change_id, path)

    @app.post("/api/runs/{change_id}/cleanup")
    def cleanup(change_id: str):
        from factory_app.context import use_project

        p = session.require()
        if session.runs.busy(p, change_id):
            raise FactoryError("el run esta avanzando; espera a que se detenga")
        with use_project(p):
            removed = remove_worktree(change_id)
        return {"removed": removed}

    @app.post("/api/runs/{change_id}/export")
    def export(change_id: str):
        return services.export_evidence(session.require(), change_id)

    # ------------------------------------------------------------ perfiles
    @app.get("/api/profiles")
    def profiles():
        return {"items": [p.model_dump() for p in store.profiles()]}

    @app.post("/api/profiles")
    def create_profile(body: ProfileBody):
        return store.save_profile(Profile(name=body.name, roles=body.roles)).model_dump()

    @app.put("/api/profiles/{profile_id}")
    def update_profile(profile_id: str, body: ProfileBody):
        if store.profile(profile_id) is None:
            raise FactoryError("Perfil no encontrado")
        return store.save_profile(
            Profile(id=profile_id, name=body.name, roles=body.roles)
        ).model_dump()

    @app.delete("/api/profiles/{profile_id}")
    def delete_profile(profile_id: str):
        return {"deleted": store.delete_profile(profile_id)}

    # ------------------------------------------------------------ eventos (SSE)
    @app.get("/api/events")
    async def events(request: Request, max_events: int = 0):
        async def stream():
            last, sent, idle = None, 0, 0.0
            while True:
                if await request.is_disconnected():
                    break
                snap = {**runs_mod.snapshot(session.project, session.runs),
                        "probes": probe_mod.version}
                if snap != last:
                    last = snap
                    sent += 1
                    yield f"event: change\ndata: {json.dumps(snap)}\n\n"
                    if max_events and sent >= max_events:
                        break
                    idle = 0.0
                elif idle >= 15:
                    yield ": ping\n\n"
                    idle = 0.0
                await asyncio.sleep(0.7)
                idle += 0.7

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    # ------------------------------------------------------------ frontend
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        supplied = request.query_params.get("token")
        if supplied:
            if not secrets.compare_digest(supplied, token):
                return HTMLResponse("Token invalido", status_code=401)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict")
            return response
        if request.cookies.get(COOKIE) != token:
            return HTMLResponse(
                "<h1>Factory Studio</h1><p>Abre la URL con token que mostró la terminal.</p>",
                status_code=401,
            )
        index_file = STATIC_DIR / "index.html"
        if not index_file.is_file():
            return HTMLResponse(
                "<h1>Factory Studio</h1><p>Frontend sin compilar: ejecuta "
                "<code>npm install && npm run build</code> en <code>ui/</code>.</p>"
            )
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    return app


def last_project(store: Store) -> Path | None:
    """Ultimo proyecto abierto que siga existiendo (se abre al iniciar sin ruta)."""
    return next((Path(p) for p in store.recent() if Path(p).is_dir()), None)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="python -m factory_ui", description="Factory Studio (local)"
    )
    parser.add_argument("project", nargs="?", default=None, help="carpeta del proyecto")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--token", help="token fijo (solo desarrollo del frontend)")
    args = parser.parse_args(argv)
    token = args.token or secrets.token_urlsafe(24)
    hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}
    root = args.project or last_project(Store()) or Path.cwd()
    app = create_app(root, token=token, allowed_hosts=hosts)
    url = f"http://127.0.0.1:{args.port}/?token={token}"
    print(f"Factory Studio: {url}")
    print(f"Proyecto: {app.state.session.project.root if app.state.session.project else root}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
