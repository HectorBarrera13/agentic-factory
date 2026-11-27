"""Configuracion por proyecto: la fabrica trabaja sobre CUALQUIER repo, no solo CampusFlow.

Cada proyecto declara en `<root>/.factory/config.toml` donde viven sus CRs, runs y
worktrees, que codigo puede escribir la fabrica, que documentos son requisitos/spec,
como se verifica, y la configuracion de agentes, topes y roles. Sin archivo se usan los
defaults (`.factory/...`, `src/` + `tests/`).

El orden de fases y gates NO es configurable: las garantias del motor (gates bloqueantes,
sin pre-aprobaciones, sin merge automatico) son las mismas para todo proyecto.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

CONFIG_DIR = ".factory"
CONFIG_FILE = "config.toml"
ROLES = ("intake", "planner", "coder", "test_analyst", "reviewer", "security", "evidence")
GATES = ("requirements", "plan", "review", "findings", "release")
VERIFY_KINDS = ("pytest", "jest", "ruff", "lint", "exit_code")
LANGUAGES = ("python", "javascript", "typescript", "other")

DEFAULT_GATE_ROLES: dict[str, list[str]] = {
    # Gate 1 (§10): "Product Owner y/o Tech Lead" aprueban alcance y plan.
    "requirements": ["product owner"],
    "plan": ["tech lead", "product owner"],
    "review": ["human reviewer", "reviewer"],
    "findings": ["human reviewer", "reviewer"],
    "release": ["release manager"],
}

# Catalogo cerrado de roles humanos que pueden firmar (perfiles y gates eligen de aqui).
ROLE_CATALOG = ("product owner", "tech lead", "human reviewer", "reviewer", "release manager")
# Proveedores de modelos (ver factory_app/providers.py).
PROVIDER_IDS = ("gemini", "anthropic", "openai", "ollama", "openai_compatible")
ProviderId = Literal["gemini", "anthropic", "openai", "ollama", "openai_compatible"]

# Lo que ningun agente escribe en ningun proyecto (ademas de `code.never_write`).
ALWAYS_NEVER_WRITE = (".git", ".factory", ".env")


def _rel(value: str) -> str:
    """Ruta relativa POSIX normalizada; rechaza absolutas y escapes (`..`)."""
    raw = str(value).strip().replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"ruta invalida (debe ser relativa al proyecto): {value!r}")
    text = raw.strip("/")
    if not text:
        raise ValueError(f"ruta invalida (debe ser relativa al proyecto): {value!r}")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"ruta invalida (no puede salir del proyecto): {value!r}")
    return "/".join(parts)


class Paths(BaseModel):
    change_requests: str = ".factory/change_requests"
    runs: str = ".factory/runs"
    worktrees: str = ".factory/worktrees"
    evidence: str = ".factory/evidence"

    @field_validator("*")
    @classmethod
    def _relative(cls, v: str) -> str:
        return _rel(v)


class Code(BaseModel):
    source: list[str] = Field(default_factory=lambda: ["src"])
    tests: str = "tests"
    # areas extra que los agentes pueden LEER (no escribir)
    readable: list[str] = Field(default_factory=list)
    never_write: list[str] = Field(default_factory=list)
    language: Literal["python", "javascript", "typescript", "other"] = "python"

    @field_validator("source", "readable", "never_write")
    @classmethod
    def _relative_list(cls, v: list[str]) -> list[str]:
        return [_rel(p) for p in v]

    @field_validator("tests")
    @classmethod
    def _relative_tests(cls, v: str) -> str:
        return _rel(v)

    @field_validator("source")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("code.source necesita al menos un directorio")
        return v

    @property
    def writable(self) -> list[str]:
        return [*self.source, self.tests]


class Docs(BaseModel):
    requirements: str | None = "docs/REQUIREMENTS.md"
    spec: str | None = "docs/SPEC.md"
    architecture: str | None = "docs/ARCHITECTURE.md"
    repo_map: str | None = None
    # secciones de requisitos que siempre entran al contexto (p.ej. restriccion arquitectonica)
    always_include: list[str] = Field(default_factory=list)

    @field_validator("requirements", "spec", "architecture", "repo_map")
    @classmethod
    def _relative(cls, v: str | None) -> str | None:
        return _rel(v) if v else None

    def files(self) -> list[str]:
        return [p for p in (self.requirements, self.spec, self.architecture, self.repo_map) if p]


class VerifyCommand(BaseModel):
    name: str
    # `{python}` = interprete que corre la fabrica (proyectos que comparten su venv)
    cmd: list[str]
    kind: Literal["pytest", "jest", "ruff", "lint", "exit_code"] = "exit_code"

    @field_validator("cmd", mode="before")
    @classmethod
    def _split(cls, v):
        if isinstance(v, str):
            import shlex

            v = shlex.split(v, posix=True)
        if not v:
            raise ValueError("cmd vacio")
        return v

    def argv(self) -> list[str]:
        import shutil

        argv = [sys.executable if part == "{python}" else part for part in self.cmd]
        # Windows: `npm`, `npx`... son .cmd; sin shell hay que resolver la ruta real
        resolved = shutil.which(argv[0]) if argv[0] != sys.executable else None
        return [resolved or argv[0], *argv[1:]]

    def label(self) -> str:
        return " ".join("python" if part == "{python}" else part for part in self.cmd)


class Verify(BaseModel):
    commands: list[VerifyCommand] = Field(default_factory=list)
    timeout: int = Field(default=120, ge=5, le=3600)


class Git(BaseModel):
    base_branch: str = "main"
    branch_prefix: str = "factory/"

    @field_validator("branch_prefix")
    @classmethod
    def _prefix(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._/-]+/", v):
            raise ValueError("branch_prefix debe terminar en '/' y usar [A-Za-z0-9._/-]")
        return v


class AgentSettings(BaseModel):
    # None = hereda el valor por defecto de [agents]
    provider: ProviderId | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    @field_validator("model")
    @classmethod
    def _model_name(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None  # vacio = hereda
        if any(c.isspace() for c in v.strip()):
            raise ValueError("nombre de modelo con espacios")
        return v.strip()


class Agents(BaseModel):
    provider: ProviderId = "gemini"
    model: str = "gemini-3.8-flash"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    roles: dict[str, AgentSettings] = Field(default_factory=dict)

    @field_validator("model")
    @classmethod
    def _model_name(cls, v: str) -> str:
        v = v.strip()
        if not v or any(c.isspace() for c in v):
            raise ValueError("nombre de modelo vacio o con espacios")
        return v

    @model_validator(mode="after")
    def _override_needs_model(self) -> Agents:
        for role, s in self.roles.items():
            if s.provider and s.provider != self.provider and not s.model:
                raise ValueError(f"el agente {role!r} cambia de proveedor: elige tambien su modelo")
        return self

    @field_validator("roles")
    @classmethod
    def _known_roles(cls, v: dict[str, AgentSettings]) -> dict[str, AgentSettings]:
        unknown = sorted(set(v) - set(ROLES))
        if unknown:
            raise ValueError(f"roles de agente desconocidos: {unknown}")
        return v

    def provider_for(self, role: str) -> str:
        return (self.roles.get(role) or AgentSettings()).provider or self.provider

    def model_for(self, role: str) -> str:
        override = (self.roles.get(role) or AgentSettings()).model
        if override:
            return override
        # FACTORY_MODEL (env) cambia el pool de cuota de Gemini sin tocar el proyecto (§20);
        # no aplica a otros proveedores (un nombre de Gemini no sirve ahi).
        if self.provider_for(role) == "gemini" and os.getenv("FACTORY_MODEL"):
            return os.environ["FACTORY_MODEL"]
        return self.model

    def provider_model_for(self, role: str) -> tuple[str, str]:
        return self.provider_for(role), self.model_for(role)

    def in_use(self) -> list[tuple[str, str]]:
        """Pares (proveedor, modelo) distintos que usan los 7 agentes."""
        seen: list[tuple[str, str]] = []
        for role in ROLES:
            pair = self.provider_model_for(role)
            if pair not in seen:
                seen.append(pair)
        return seen

    def temperature_for(self, role: str) -> float:
        override = (self.roles.get(role) or AgentSettings()).temperature
        return self.temperature if override is None else override


class Limits(BaseModel):
    verify_rounds: int = Field(default=3, ge=1, le=10)
    review_rounds: int = Field(default=3, ge=1, le=10)


class Gates(BaseModel):
    roles: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_GATE_ROLES))

    @field_validator("roles")
    @classmethod
    def _complete(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        merged = {**DEFAULT_GATE_ROLES, **v}
        unknown = sorted(set(merged) - set(GATES))
        if unknown:
            raise ValueError(f"gates desconocidos: {unknown}")
        for gate, roles in merged.items():
            cleaned = list(dict.fromkeys(r.strip().lower() for r in roles if r.strip()))
            if not cleaned:
                raise ValueError(f"el gate {gate!r} necesita al menos un rol firmante")
            extra = [r for r in cleaned if r not in ROLE_CATALOG]
            if extra:
                raise ValueError(
                    f"roles fuera del catalogo en {gate!r}: {extra} (validos: {list(ROLE_CATALOG)})"
                )
            merged[gate] = cleaned
        return merged


class Scanners(BaseModel):
    # vacio => code.source
    semgrep_targets: list[str] = Field(default_factory=list)

    @field_validator("semgrep_targets")
    @classmethod
    def _relative_list(cls, v: list[str]) -> list[str]:
        return [_rel(p) for p in v]


class Ids(BaseModel):
    """Formato de ids de los documentos del proyecto (trazabilidad y seleccion de contexto)."""

    acceptance: str = r"AC-[A-Za-z0-9]+\.\d+"
    requirement: str = r"\b(?:N?FR)-\d+\b"

    @field_validator("acceptance", "requirement")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v


class ProjectConfig(BaseModel):
    name: str = ""
    paths: Paths = Field(default_factory=Paths)
    code: Code = Field(default_factory=Code)
    docs: Docs = Field(default_factory=Docs)
    verify: Verify = Field(default_factory=Verify)
    git: Git = Field(default_factory=Git)
    agents: Agents = Field(default_factory=Agents)
    limits: Limits = Field(default_factory=Limits)
    gates: Gates = Field(default_factory=Gates)
    scanners: Scanners = Field(default_factory=Scanners)
    ids: Ids = Field(default_factory=Ids)

    @model_validator(mode="after")
    def _code_not_in_factory_areas(self) -> ProjectConfig:
        factory_areas = [self.paths.runs, self.paths.change_requests, self.paths.worktrees]
        for area in self.code.writable:
            for fa in [*factory_areas, *ALWAYS_NEVER_WRITE]:
                if area == fa or area.startswith(fa + "/") or fa.startswith(area + "/"):
                    raise ValueError(
                        f"el area escribible {area!r} se solapa con {fa!r} (area de la fabrica)"
                    )
        return self


class Project:
    """Un proyecto abierto: raiz + configuracion validada."""

    def __init__(self, root: Path, config: ProjectConfig | None = None):
        self.root = Path(root).resolve()
        self.config = config or ProjectConfig()

    def __repr__(self) -> str:  # pragma: no cover
        return f"Project({self.root})"

    @property
    def name(self) -> str:
        return self.config.name or self.root.name

    @property
    def runs_dir(self) -> Path:
        return self.root / self.config.paths.runs

    @property
    def cr_dir(self) -> Path:
        return self.root / self.config.paths.change_requests

    @property
    def worktrees_dir(self) -> Path:
        return self.root / self.config.paths.worktrees

    @property
    def evidence_dir(self) -> Path:
        return self.root / self.config.paths.evidence

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_DIR / CONFIG_FILE

    def run_dir(self, change_id: str) -> Path:
        return self.runs_dir / normalize_change_id(change_id)

    def worktree(self, change_id: str) -> Path:
        return self.worktrees_dir / normalize_change_id(change_id)

    def work_root(self, change_id: str | None) -> Path:
        """Donde vive el codigo de un run: su worktree si existe; si no, la raiz."""
        if change_id:
            try:
                wt = self.worktree(change_id)
            except ValueError:  # carpeta que no es un run (p.ej. sandbox de tests)
                return self.root
            if (wt / ".git").exists():
                return wt
        return self.root

    def branch(self, change_id: str) -> str:
        return f"{self.config.git.branch_prefix}{normalize_change_id(change_id)}"

    def is_python(self) -> bool:
        return self.config.code.language == "python"

    def semgrep_targets(self) -> list[str]:
        return self.config.scanners.semgrep_targets or list(self.config.code.source)


_CR_RE = re.compile(r"^CR-[A-Z0-9][A-Z0-9-]{0,40}$")


def normalize_change_id(change_id: str) -> str:
    cid = str(change_id).strip().upper()
    if not _CR_RE.match(cid):
        raise ValueError(f"change id invalido: {change_id!r} (formato CR-001)")
    return cid


def load_project(root: Path | str) -> Project:
    root = Path(root).resolve()
    path = root / CONFIG_DIR / CONFIG_FILE
    if path.is_file():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return Project(root, ProjectConfig.model_validate(data))
    return Project(root, ProjectConfig())


def save_config(project: Project, config: ProjectConfig) -> Path:
    """Escribe `.factory/config.toml` (y `.factory/.gitignore` para worktrees/runs locales)."""
    path = project.config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_toml(config.model_dump(exclude_none=True)), encoding="utf-8")
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("worktrees/\nruns/\nevidence/\n", encoding="utf-8")
    project.config = config
    return path


# ---------------------------------------------------------------- TOML minimo
def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"valor TOML no soportado: {type(value).__name__}")


def _key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _toml_value(name)


def dumps_toml(data: dict, prefix: str = "") -> str:
    """Serializa dicts de escalares/listas/tablas y listas de tablas (lo que usa la config)."""
    scalars, tables, arrays = [], [], []
    for k, v in data.items():
        if isinstance(v, dict):
            tables.append((k, v))
        elif isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
            arrays.append((k, v))
        else:
            scalars.append(f"{_key(k)} = {_toml_value(v)}")
    out = "\n".join(scalars)
    for k, v in tables:
        name = f"{prefix}{_key(k)}"
        body = dumps_toml(v, name + ".")
        head = f"[{name}]"
        out += ("\n\n" if out else "") + head + ("\n" + body if body else "")
    for k, items in arrays:
        name = f"{prefix}{_key(k)}"
        for item in items:
            out += ("\n\n" if out else "") + f"[[{name}]]\n" + dumps_toml(item, name + ".")
    return out.strip() + "\n" if not prefix else out.strip()
