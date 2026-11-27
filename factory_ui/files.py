"""Acceso a archivos del editor, siempre dentro del proyecto abierto.

Dos raices:
- `user`: el arbol del usuario (editable).
- `run:<CR>`: el worktree de un run (SOLO lectura: lo escribe la fabrica, se revisa aqui).

Nunca se sirve `.git`; los archivos de secretos (`.env*`, claves) se listan pero su
contenido no se envia ni se puede escribir desde la UI.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from factory_app.project import Project
from factory_app.roles import FactoryError

HIDDEN_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
               ".ruff_cache", ".mypy_cache", "dist", "build", ".next", ".idea"}
SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*.p12",
                   "*.pfx", "credentials.json", "secrets.*")
MAX_EDITABLE = 2_000_000
MAX_TREE_ENTRIES = 5_000


TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def is_secret(name: str) -> bool:
    lower = name.lower()
    if lower.endswith(TEMPLATE_SUFFIXES):  # .env.example & co. son plantillas sin valores
        return False
    return any(fnmatch.fnmatch(lower, pat) for pat in SECRET_PATTERNS)


def root_for(project: Project, root: str) -> tuple[Path, bool]:
    """(ruta raiz, solo_lectura)."""
    if root in ("", "user"):
        return project.root, False
    if root.startswith("run:"):
        try:
            wt = project.worktree(root[4:])
        except ValueError as exc:
            raise FactoryError(str(exc)) from exc
        if not (wt / ".git").exists():
            raise FactoryError(f"El run {root[4:]} no tiene worktree (aun no paso el gate plan)")
        return wt, True
    raise FactoryError(f"raiz desconocida: {root!r}")


def resolve(project: Project, root: str, rel: str) -> tuple[Path, Path, bool]:
    base, readonly = root_for(project, root)
    rel = str(rel).replace("\\", "/").strip().lstrip("/")
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise FactoryError("ruta fuera del proyecto")
    parts = target.relative_to(base).parts
    if ".git" in parts:
        raise FactoryError(".git no es accesible desde la UI")
    if base == project.root and _inside_worktrees(project, target):
        raise FactoryError("los worktrees de runs se abren con la raiz run:<CR>")
    return base, target, readonly


def _inside_worktrees(project: Project, target: Path) -> bool:
    wt = project.worktrees_dir.resolve()
    return target == wt or wt in target.parents


def tree(project: Project, root: str) -> dict:
    base, readonly = root_for(project, root)
    count = 0
    skip = {project.worktrees_dir.resolve()} if base == project.root else set()

    def walk(folder: Path) -> list[dict]:
        nonlocal count
        entries = []
        try:
            children = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return entries
        for child in children:
            if count >= MAX_TREE_ENTRIES:
                break
            if child.name in HIDDEN_DIRS or child.resolve() in skip:
                continue
            count += 1
            rel = child.relative_to(base).as_posix()
            if child.is_dir():
                entries.append({"name": child.name, "path": rel, "type": "dir",
                                "children": walk(child)})
            else:
                entries.append({"name": child.name, "path": rel, "type": "file",
                                "secret": is_secret(child.name)})
        return entries

    return {"root": root or "user", "readonly": readonly, "truncated": count >= MAX_TREE_ENTRIES,
            "entries": walk(base)}


def read(project: Project, root: str, rel: str) -> dict:
    _base, target, readonly = resolve(project, root, rel)
    if not target.is_file():
        raise FactoryError("el archivo no existe")
    info = {"path": rel, "readonly": readonly, "secret": False, "binary": False, "content": ""}
    if is_secret(target.name):
        return {**info, "secret": True, "readonly": True}
    if target.stat().st_size > MAX_EDITABLE:
        return {**info, "binary": True, "readonly": True}
    data = target.read_bytes()
    if b"\x00" in data[:8000]:
        return {**info, "binary": True, "readonly": True}
    try:
        return {**info, "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {**info, "binary": True, "readonly": True}


def write(project: Project, root: str, rel: str, content: str) -> dict:
    _base, target, readonly = resolve(project, root, rel)
    if readonly:
        raise FactoryError("el worktree de un run es de solo lectura (lo escribe la fabrica)")
    if is_secret(target.name):
        raise FactoryError("los archivos de secretos no se editan desde la UI")
    if len(content.encode("utf-8")) > MAX_EDITABLE:
        raise FactoryError("archivo demasiado grande para el editor")
    if target.is_dir():
        raise FactoryError("la ruta es una carpeta")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="")
    return {"path": rel, "bytes": len(content.encode("utf-8"))}


def git_status(project: Project) -> list[dict]:
    r = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"], cwd=project.root, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        if len(line) > 3:
            out.append({"status": line[:2].strip() or "?", "path": line[3:].strip('"')})
    return out
