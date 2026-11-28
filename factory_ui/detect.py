"""Asistente "Abrir proyecto": propone una config a partir de lo que hay en la carpeta.

Solo sugiere; el humano revisa y guarda. Nada se escribe aqui.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from factory_app.project import ProjectConfig

TEST_DIR_CANDIDATES = ("tests", "test", "spec", "__tests__")
DOC_CANDIDATES = {
    "requirements": ("docs/REQUIREMENTS.md", "REQUIREMENTS.md", "docs/requirements.md"),
    "spec": ("docs/SPEC.md", "SPEC.md", "docs/spec.md"),
    "architecture": ("docs/ARCHITECTURE.md", "ARCHITECTURE.md", "docs/architecture.md"),
    "repo_map": ("docs/REPO_MAP.md", "REPO_MAP.md"),
}


# carpetas que nunca son "el codigo" del proyecto aunque tengan archivos del lenguaje
NOT_SOURCE_DIRS = {
    "node_modules", "dist", "build", "out", "coverage", "vendor", "docs", "scripts",
    "results", *TEST_DIR_CANDIDATES,
}
CODE_SUFFIXES = {
    "javascript": (".js", ".mjs", ".cjs", ".jsx"),
    "typescript": (".ts", ".tsx", ".mts", ".cts", ".js", ".mjs"),
}


def _code_dirs(root: Path, suffixes: tuple[str, ...]) -> list[str]:
    """Carpetas de primer nivel con codigo del lenguaje (mira hasta dos niveles)."""
    found = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name in NOT_SOURCE_DIRS:
            continue
        files = [*d.glob("*"), *d.glob("*/*")]
        if any(f.is_file() and f.suffix in suffixes for f in files):
            found.append(d.name)
    return found


def _first(root: Path, candidates) -> str | None:
    return next((c for c in candidates if (root / c).exists()), None)


def _python(root: Path) -> dict:
    data = {}
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            data = {}
    source = []
    if (root / "src").is_dir():
        source = ["src"]
    else:
        source = sorted(
            p.name for p in root.iterdir()
            if p.is_dir() and (p / "__init__.py").is_file()
            and p.name not in {"tests", "test", "scripts"} and not p.name.startswith(".")
        )
    runner = ["uv", "run"] if (root / "uv.lock").is_file() else ["{python}", "-m"]
    commands = [{"name": "tests", "cmd": [*runner, "pytest", "-q"], "kind": "pytest"}]
    tool = data.get("tool", {}) if isinstance(data, dict) else {}
    if "ruff" in tool or (root / "ruff.toml").is_file():
        commands.append({"name": "lint", "cmd": [*runner, "ruff", "check", "."], "kind": "ruff"})
    return {
        "name": (data.get("project") or {}).get("name", "") if isinstance(data, dict) else "",
        "language": "python",
        "source": source or ["src"],
        "commands": commands,
    }


def _node(root: Path) -> dict:
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pkg = {}
    scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})} if pkg else {}
    language = "typescript" if (root / "tsconfig.json").is_file() or "typescript" in deps \
        else "javascript"
    commands = []
    if "test" in scripts:
        kind = "jest" if "jest" in deps or "jest" in str(scripts.get("test")) else "exit_code"
        commands.append({"name": "tests", "cmd": ["npm", "test"], "kind": kind})
    if "lint" in scripts:
        commands.append({"name": "lint", "cmd": ["npm", "run", "lint"], "kind": "lint"})
    if "build" in scripts:
        commands.append({"name": "build", "cmd": ["npm", "run", "build"], "kind": "exit_code"})
    if (root / "src").is_dir():
        source = ["src"]
    elif (root / "app").is_dir():
        source = ["app"]
    else:
        # p. ej. un proyecto vanilla con js/ y css/: antes se proponia "src", que no existia
        source = _code_dirs(root, CODE_SUFFIXES[language]) or ["src"]
    return {
        "name": pkg.get("name", "") if isinstance(pkg, dict) else "",
        "language": language,
        "source": source,
        "commands": commands,
    }


def detect(root: Path) -> dict:
    """{"config": ProjectConfig sugerida (dict), "notes": [...]} para revisar en el asistente."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(str(root))
    notes: list[str] = []
    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file():
        found = _python(root)
        notes.append("Proyecto Python detectado (pyproject/requirements).")
    elif (root / "package.json").is_file():
        found = _node(root)
        notes.append(f"Proyecto {found['language']} detectado (package.json).")
    else:
        found = {"name": "", "language": "other", "source": ["src"], "commands": []}
        notes.append("No se detecto el lenguaje: revisa areas y comandos.")
    if not found["commands"]:
        notes.append("Sin comandos de verificacion: la verificacion fallara hasta configurarlos.")
    if not (root / ".git").exists():
        notes.append("La carpeta no es un repo git: sin git no hay rama ni worktree por run.")
    missing = [s for s in found["source"] if not (root / s).exists()]
    if missing:
        notes.append(
            "No se encontro la carpeta de codigo " + ", ".join(missing)
            + ": indica en Rutas y codigo donde esta el codigo; sin eso los agentes no pueden"
            " leerlo ni cambiarlo."
        )
    tests = _first(root, TEST_DIR_CANDIDATES) or "tests"
    docs = {key: _first(root, cands) for key, cands in DOC_CANDIDATES.items()}
    if not docs["requirements"]:
        notes.append("Sin documento de requisitos: el contexto de los agentes sera solo el CR.")
    config = ProjectConfig.model_validate(
        {
            "name": found["name"] or root.name,
            "code": {"source": found["source"], "tests": tests, "language": found["language"]},
            "docs": docs,
            "verify": {"commands": found["commands"]},
        }
    )
    return {"config": config.model_dump(), "notes": notes,
            "has_config": (root / ".factory" / "config.toml").is_file()}
