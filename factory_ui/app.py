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
import secrets
import webbrowser
from pathlib import Path
from typing import Literal

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from factory_app import services
from factory_app.orchestrator import remove_worktree
from factory_app.project import (
    GATES,
    LANGUAGES,
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


class StartBody(BaseModel):
    change_id: str


class AdvanceBody(BaseModel):
    mode: Literal["fake", "adk"] = "fake"


class GateBody(BaseModel):
    decision: str
    reason: str
    profile_id: str
    role: str


class FindingsBody(BaseModel):
    verdicts: dict[str, str]
    profile_id: str
    role: str


class ProfileBody(BaseModel):
    name: str
    roles: list[str]


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
    if role not in profile.roles:
        raise FactoryError(f"El perfil {profile.name} no tiene el rol {role!r}")
    return profile.signature(role)


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
        return JSONResponse({"error": str(exc)}, status_code=400)

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
            "defaults": ProjectConfig().model_dump(),
        }

    @app.post("/api/projects/open")
    def open_project(body: OpenBody):
        session.open(body.path)
        return _project_info(session)

    @app.post("/api/projects/detect")
    def detect(body: OpenBody):
        try:
            return detect_mod.detect(Path(body.path).expanduser())
        except FileNotFoundError as exc:
            raise FactoryError(f"La carpeta no existe: {body.path}") from exc

    @app.get("/api/config")
    def get_config():
        return _project_info(session) or {}

    @app.put("/api/config")
    def put_config(data: dict = Body(...)):  # noqa: B008
        p = session.require()
        config = ProjectConfig.model_validate(data)
        save_config(p, config)
        return _project_info(session)

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
    def list_crs():
        return {"items": services.list_change_requests(session.require())}

    @app.get("/api/crs/{change_id}")
    def get_cr(change_id: str):
        p = session.require()
        path = services.find_change_request(p, change_id)
        return {
            "change_id": change_id.upper(),
            "file": path.relative_to(p.root).as_posix(),
            "body": path.read_text(encoding="utf-8"),
            "frozen": (p.run_dir(change_id) / "factory-state.json").is_file(),
        }

    @app.post("/api/crs")
    def create_cr(body: CrBody):
        p = session.require()
        path = services.create_change_request(p, body.change_id, body.title, body.body)
        return {"file": path.relative_to(p.root).as_posix()}

    @app.put("/api/crs/{change_id}")
    def update_cr(change_id: str, body: CrUpdate):
        p = session.require()
        path = services.update_change_request(p, change_id, body.body)
        return {"file": path.relative_to(p.root).as_posix()}

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

    @app.get("/api/runs/{change_id}/artifacts/{name}")
    def run_artifact(change_id: str, name: str):
        return {"name": name, "content": runs_mod.read_artifact(session.require(), change_id, name)}

    @app.post("/api/runs/{change_id}/advance")
    def advance(change_id: str, body: AdvanceBody, wait: bool = False):
        return session.runs.advance(session.require(), change_id, body.mode, wait=wait)

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
                snap = runs_mod.snapshot(session.project, session.runs)
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
    app = create_app(args.project or Path.cwd(), token=token, allowed_hosts=hosts)
    url = f"http://127.0.0.1:{args.port}/?token={token}"
    print(f"Factory Studio: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
