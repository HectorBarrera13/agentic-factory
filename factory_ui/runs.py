"""Runs desde la UI: avance en segundo plano (un hilo y un lock por run) y vista del run.

`advance` llama al MISMO `orchestrator.advance` que la CLI: las paradas en gates, los
escalamientos y los bloqueos se reportan como "outcome" para la UI, nunca se saltan.
"""

from __future__ import annotations

import contextvars
import json
import subprocess
import threading
import time
from pathlib import Path

from factory_app import activity, control, services
from factory_app import orchestrator as ORCH
from factory_app.context import FACTORY_REPO, use_project
from factory_app.project import Project, normalize_change_id
from factory_app.roles import FactoryError

# Que debe mirar el humano en cada gate (artefactos del run).
GATE_ARTIFACTS = {
    "requirements": ["01-intake-report.md"],
    "plan": ["03-implementation-plan.md"],
    "review": ["08-review-report.md", "09-security-report.md", "07-verification.txt"],
    "release": ["14-evidence-summary.md", "traceability.md", "factory-run.json"],
}
GATE_QUESTION = {
    "requirements": "¿La interpretación del Change Request y su alcance son correctos?",
    "plan": "¿Apruebas este plan? El Coder no escribe nada hasta que lo apruebes.",
    "review": "Clasifica cada finding y decide si el run continúa.",
    "release": "Con la evidencia consolidada, ¿está listo para merge humano?",
}


class RunManager:
    def __init__(self):
        self._locks: dict[str, threading.Lock] = {}
        self._outcomes: dict[str, dict] = {}
        self._controls: dict[str, control.RunControl] = {}
        self._guard = threading.Lock()
        self.version = 0  # cambia con cada outcome (lo mira el SSE)

    def _key(self, project: Project, cid: str) -> str:
        return f"{project.root}::{cid}"

    def busy(self, project: Project, cid: str) -> bool:
        lock = self._locks.get(self._key(project, cid))
        return bool(lock and lock.locked())

    def outcome(self, project: Project, cid: str) -> dict | None:
        return self._outcomes.get(self._key(project, cid))

    def _executor(self, mode: str):
        if mode == "fake":
            return ORCH.FakeExecutor()
        if mode == "adk":
            try:
                from dotenv import load_dotenv

                load_dotenv(FACTORY_REPO / ".env")
            except ImportError:  # pragma: no cover
                pass
            from factory_app import credentials

            credentials.apply_to_env()  # claves guardadas desde la UI (el entorno real gana)
            return ORCH.ADKExecutor()
        raise FactoryError(f"modo desconocido: {mode!r} (fake | adk)")

    def advance(self, project: Project, change_id: str, mode: str, wait: bool = False) -> dict:
        cid = normalize_change_id(change_id)
        services.read_state(project, cid)  # FactoryError si no existe
        executor = self._executor(mode)
        key = self._key(project, cid)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
            if not lock.acquire(blocking=False):
                raise FactoryError(f"{cid} ya esta avanzando; espera a que se detenga")
        ctl = control.RunControl()
        self._controls[key] = ctl
        self._set(key, {"kind": "running", "mode": mode, "at": time.time()})

        def work():
            try:
                with use_project(project), control.use_control(ctl):
                    st = ORCH.advance(cid, executor)
                self._set(key, {"kind": "done", "status": st["status"]})
            except control.Paused as exc:
                self._set(key, {"kind": "paused", "message": str(exc), "mode": mode})
            except ORCH.AwaitingHuman as exc:
                self._set(key, {"kind": "awaiting", "gate": exc.gate, "message": exc.why})
            except ORCH.Blocked as exc:
                self._set(key, {"kind": "blocked", "message": str(exc)})
            except ORCH.TransientModelError as exc:
                self._set(key, {"kind": "transient", "message": str(exc)})
            except SystemExit as exc:
                self._set(key, {"kind": "error", "message": str(exc)})
            except Exception as exc:  # noqa: BLE001 — se reporta a la UI, el run queda en checkpoint
                self._set(key, {"kind": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                self._controls.pop(key, None)
                lock.release()

        ctx = contextvars.copy_context()
        thread = threading.Thread(target=ctx.run, args=(work,), daemon=True, name=f"run-{cid}")
        thread.start()
        if wait:
            thread.join()
        return self.outcome(project, cid) or {}

    def pause(self, project: Project, change_id: str) -> dict:
        """Pausa inmediata: cancela la llamada en curso; el run vuelve a su checkpoint."""
        cid = normalize_change_id(change_id)
        key = self._key(project, cid)
        ctl = self._controls.get(key)
        if ctl is None or not self.busy(project, cid):
            raise FactoryError(f"{cid} no esta avanzando: no hay nada que pausar")
        ctl.pause()
        self._set(key, {**(self._outcomes.get(key) or {}), "kind": "running", "pausing": True})
        return self.outcome(project, cid) or {}

    def _set(self, key: str, outcome: dict) -> None:
        self._outcomes[key] = {**outcome, "at": time.time()}
        self.version += 1


def _artifacts(run: Path) -> list[dict]:
    if not run.is_dir():
        return []
    return [
        {"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime}
        for f in sorted(run.iterdir())
        if f.is_file()
    ]


def _findings(project: Project, run: Path, state: dict) -> dict:
    ids = sorted(services.report_finding_ids(run))
    with use_project(project):
        severities = ORCH.finding_severities(run)
        reports = [ORCH._read(run / n) for n in ("08-review-report.md", "09-security-report.md")]
        segments = ORCH.finding_segments("\n".join(reports))
    path = run / "human-findings-decisions.json"
    decided = {}
    if path.is_file() and state.get("awaiting_gate") == "review":
        data = json.loads(path.read_text(encoding="utf-8"))
        decided = {d["finding_id"]: d["decision"] for d in data.get("decisions", [])}
    return {
        "items": [
            {
                "id": fid,
                "severity": severities.get(fid, "UNKNOWN"),
                "blocking": severities.get(fid, "MUST FIX") in ORCH.BLOCKING_SEVERITIES,
                "decision": decided.get(fid),
                "excerpt": segments.get(fid, "").strip()[:600],
            }
            for fid in ids
        ],
        "pending": [fid for fid in ids if fid not in decided],
    }


def detail(project: Project, manager: RunManager, change_id: str) -> dict:
    cid = normalize_change_id(change_id)
    state = services.read_state(project, cid)
    run = project.run_dir(cid)
    gate = state.get("awaiting_gate") or ""
    roles = project.config.gates.roles
    # Decision ya registrada para el gate abierto (la toma el motor en el siguiente advance).
    recorded = None
    if gate:
        with use_project(project):
            decision = ORCH._gate_decision(run, gate, state)
        if decision:
            recorded = {k: decision.get(k) for k in ("decision", "reason", "recorded_by",
                                                       "recorded_at")}
    return {
        "change_id": cid,
        "state": state,
        "busy": manager.busy(project, cid),
        "outcome": manager.outcome(project, cid),
        "artifacts": _artifacts(run),
        "gate": {
            "name": gate,
            "question": GATE_QUESTION.get(gate, ""),
            "artifacts": [a for a in GATE_ARTIFACTS.get(gate, []) if (run / a).is_file()],
            "roles": roles.get(gate, []),
            "findings_roles": roles.get("findings", []),
            "recorded": recorded,
        }
        if gate
        else None,
        "findings": _findings(project, run, state),
        "live": activity.live(run) if manager.busy(project, cid) else None,
        "worktree": state.get("worktree") if (project.root / str(state.get("worktree") or "-"))
        .exists() else None,
    }


def read_artifact(project: Project, change_id: str, name: str) -> str:
    run = project.run_dir(change_id)
    path = (run / name).resolve()
    if path.parent != run.resolve() or not path.is_file():
        raise FactoryError(f"artefacto no encontrado: {name}")
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- diff del run
def _git(project: Project, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project.root, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )


def _range(project: Project, cid: str) -> tuple[str, str]:
    state = services.read_state(project, cid)
    base, branch = state.get("base_commit"), state.get("branch")
    if not base or not branch:
        raise FactoryError(f"{cid} aun no tiene rama (se crea al aprobar el plan)")
    return base, branch


def diff_files(project: Project, change_id: str) -> dict:
    cid = normalize_change_id(change_id)
    base, branch = _range(project, cid)
    r = _git(project, "diff", "--name-status", f"{base}...{branch}")
    if r.returncode != 0:
        raise FactoryError(r.stderr.strip() or "git diff fallo")
    files = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append({"status": parts[0][0], "path": parts[-1]})
    return {"base": base, "branch": branch, "files": files}


def diff_file(project: Project, change_id: str, path: str) -> dict:
    cid = normalize_change_id(change_id)
    base, branch = _range(project, cid)
    if ".." in path.replace("\\", "/").split("/"):
        raise FactoryError("ruta invalida")
    original = _git(project, "show", f"{base}:{path}")
    modified = _git(project, "show", f"{branch}:{path}")
    return {
        "path": path,
        "original": original.stdout if original.returncode == 0 else "",
        "modified": modified.stdout if modified.returncode == 0 else "",
    }


def snapshot(project: Project | None, manager: RunManager) -> dict:
    """Firma barata de cambios para el SSE (mtimes de estados + outcomes)."""
    if project is None:
        return {"version": manager.version}
    stamps = {}
    if project.runs_dir.is_dir():
        for path in project.runs_dir.glob("CR-*/factory-state.json"):
            try:
                stamps[path.parent.name] = path.stat().st_mtime_ns
            except OSError:
                continue
    # actividad en vivo: cada evento nuevo (fase, tool, comando) cambia el snapshot
    live = {}
    if project.runs_dir.is_dir():
        for path in project.runs_dir.glob(f"CR-*/{activity.ACTIVITY_FILE}"):
            try:
                st = path.stat()
            except OSError:
                continue
            live[path.parent.name] = [st.st_mtime_ns, st.st_size]
    crs = project.cr_dir.stat().st_mtime_ns if project.cr_dir.is_dir() else 0
    return {"version": manager.version, "runs": stamps, "crs": crs, "activity": live}
