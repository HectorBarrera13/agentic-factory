"""Tools de los agentes, acotadas por la configuracion del proyecto activo.

- El codigo se lee y escribe en el WORKTREE del run activo (`context.work_root()`): el arbol
  del usuario no se toca mientras la fabrica trabaja.
- CRs y evidencia viven en la raiz del proyecto (`paths.change_requests` / `paths.runs`).
- Lo escribible sale de `code.source` + `code.tests`; lo legible, ademas, de `code.readable`
  y los documentos de requisitos. Todo lo demas se rechaza en runtime (no por prompt).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .context import active_run, project, work_root
from .project import ALWAYS_NEVER_WRITE

BINARY_SUFFIXES = {
    ".db", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".pdf", ".ico", ".woff", ".woff2",
}
SKIP_PARTS = {"__pycache__", "node_modules"}
READ_CAP = 40_000
WRITE_CAP = 60_000


def _under(posix: str, prefixes: list[str]) -> bool:
    return any(posix == p or posix.startswith(p + "/") for p in prefixes if p)


def _factory_areas() -> list[str]:
    paths = project().config.paths
    return [paths.change_requests, paths.runs]


def _readable_areas() -> list[str]:
    cfg = project().config
    docs = []
    for f in cfg.docs.files():
        parent = f.rsplit("/", 1)[0] if "/" in f else ""
        docs.append(parent or f)
    return [*cfg.code.source, cfg.code.tests, *cfg.code.readable, *docs, *_factory_areas()]


def _resolve(relative_path: str) -> tuple[Path, Path, str] | None:
    """(ruta absoluta, raiz usada, rel posix) o None si escapa de la raiz.
    CRs/runs se resuelven contra el proyecto; el codigo contra el worktree del run."""
    rel_text = str(relative_path).strip().replace("\\", "/").lstrip("/")
    base = project().root if _under(rel_text, _factory_areas()) else work_root()
    candidate = (base / rel_text).resolve()
    if base != candidate and base not in candidate.parents:
        return None
    return candidate, base, candidate.relative_to(base).as_posix() if candidate != base else ""


def list_repo_files(prefix: str) -> dict:
    """List files under an allowlisted read-only area.

    Args:
        prefix: Folder relative to the project root (one of the readable areas: source,
            tests, docs, change requests or runs). Use "" to list every readable area.

    Returns:
        {"ok": True, "files": [...]} (max 250) or {"ok": False, "error": "..."}.
    """
    prefix = str(prefix).strip().replace("\\", "/").strip("/")
    areas = _readable_areas()
    if prefix and not (_under(prefix, areas) or any(a.startswith(prefix + "/") for a in areas)):
        return {"ok": False, "error": f"read not allowed under {prefix!r}"}
    targets = [prefix] if prefix else sorted(set(areas))
    items: list[str] = []
    for target in targets:
        resolved = _resolve(target)
        if resolved is None:
            return {"ok": False, "error": "path escapes repository"}
        root, base, _rel = resolved
        if not root.exists():
            if prefix:
                return {"ok": False, "error": "path does not exist"}
            continue
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if any(part.startswith(".") or part in SKIP_PARTS for part in rel.split("/")):
                continue
            if not _under(rel, areas):
                continue
            items.append(rel)
    return {"ok": True, "files": sorted(set(items))[:250]}


def read_repo_file(relative_path: str) -> dict:
    """Read UTF-8 text from an allowlisted project area.

    Args:
        relative_path: File path relative to the project root, e.g. "docs/SPEC.md".

    Returns:
        {"ok": True, "path": ..., "content": ...} (truncated at 40000 chars) or
        {"ok": False, "error": "..."}.
    """
    resolved = _resolve(relative_path)
    if resolved is None:
        return {"ok": False, "error": "path escapes repository"}
    path, _base, rel = resolved
    if not rel or not _under(rel, _readable_areas()) or _under(rel, list(ALWAYS_NEVER_WRITE)):
        return {"ok": False, "error": "read not allowed for this path"}
    if not path.is_file():
        return {"ok": False, "error": "file does not exist"}
    if path.suffix.lower() in BINARY_SUFFIXES:
        return {"ok": False, "error": "binary file not supported"}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "file is not UTF-8 text"}
    if len(text) > READ_CAP:
        text = text[:READ_CAP] + "\n...[TRUNCATED]..."
    return {"ok": True, "path": rel, "content": text}


def run_baseline_verification() -> dict:
    """Run the project's configured verification commands. No arbitrary shell input.

    Returns:
        {"ok": bool, "results": [{"command", "kind", "returncode", "stdout", "stderr"}, ...]}.
    """
    cfg = project().config.verify
    cwd = work_root()
    results = []
    all_ok = True
    if not cfg.commands:
        return {
            "ok": False,
            "results": [
                {
                    "command": "(sin comandos de verificacion)",
                    "kind": "exit_code",
                    "returncode": None,
                    "stdout": "",
                    "stderr": "configure [[verify.commands]] en .factory/config.toml",
                }
            ],
        }
    for command in cfg.commands:
        label = command.label()
        try:
            # sin shell: argv explicito de la config, nunca texto del LLM
            completed = subprocess.run(
                command.argv(), cwd=cwd, text=True, capture_output=True, encoding="utf-8",
                errors="replace", timeout=cfg.timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:  # FAIL registrado, no excepcion que tumbe el run
            all_ok = False
            results.append(
                {
                    "command": label,
                    "kind": command.kind,
                    "returncode": None,
                    "stdout": str(exc.stdout or "")[-6000:],
                    "stderr": f"timeout tras {cfg.timeout}s",
                }
            )
            continue
        except FileNotFoundError:
            all_ok = False
            results.append(
                {
                    "command": label,
                    "kind": command.kind,
                    "returncode": None,
                    "stdout": "",
                    "stderr": f"comando no encontrado: {command.argv()[0]}",
                }
            )
            continue
        all_ok = all_ok and completed.returncode == 0
        results.append(
            {
                "command": label,
                "kind": command.kind,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-6000:],
                "stderr": completed.stderr[-3000:],
            }
        )
    return {"ok": all_ok, "results": results}


ENGINE_ARTIFACTS = {
    "factory-state.json",
    "factory-run.json",
    "traceability.md",
    "context-manifest.json",
}


def _is_engine_artifact(filename: str) -> bool:
    """Decisiones humanas (human-*), estado/consolidados del motor y la evidencia
    numerada NN-*: nadie salvo el motor o el humano los escribe (no se forjan aprobaciones)."""
    name = filename.lower()
    return (
        name.startswith("human-")
        or name in ENGINE_ARTIFACTS
        or re.match(r"^\d{2}-", name) is not None
    )


def write_run_evidence(change_id: str, filename: str, content: str) -> dict:
    """Write only evidence files under the run folder; never product code.

    Args:
        change_id: Change Request id, e.g. "CR-001".
        filename: Plain file name (.md, .json or .txt), no folders. Engine artifacts
            (NN-*.md, human-*, factory-*.json, traceability.md...) are denied.
        content: Full text to write.

    Returns:
        {"ok": True, "path": ...} or {"ok": False, "error": "..."}.
    """
    if not str(change_id).startswith("CR-"):
        return {"ok": False, "error": "change_id must start with CR-"}
    if "/" in filename or "\\" in filename or not filename.endswith((".md", ".json", ".txt")):
        return {"ok": False, "error": "invalid evidence filename"}
    if _is_engine_artifact(filename):
        return {
            "ok": False,
            "error": f"{filename!r} lo genera el motor o un humano; el agente no puede escribirlo",
        }
    try:
        folder = project().run_dir(change_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": path.relative_to(project().root).as_posix()}


def write_product_file(relative_path: str, content: str) -> dict:
    """Bounded write for the Coder: only the project's source and test areas.

    Denies requirements/spec/docs, the factory itself, evidence, secrets and
    configuration. El motor ademas registra intentos y corre verificacion
    determinista despues; el Coder nunca puede autoaprobarse.

    Args:
        relative_path: Path under one of the writable areas (code.source or code.tests).
        content: Full new file content (max 60000 chars).

    Returns:
        {"ok": True, "path": ..., "bytes": n} or {"ok": False, "error": "..."}.
    """
    cfg = project().config
    root = work_root()
    candidate = (root / str(relative_path).replace("\\", "/")).resolve()
    if root not in candidate.parents:
        return {"ok": False, "error": "path escapes repository"}
    posix = candidate.relative_to(root).as_posix()
    denied = [*ALWAYS_NEVER_WRITE, *cfg.code.never_write, *_factory_areas()]
    top = posix.split("/", 1)[0]
    if _under(posix, denied) or top.startswith(".env"):
        return {"ok": False, "error": f"write denied for {top!r} (least privilege)"}
    if not _under(posix, cfg.code.writable):
        return {"ok": False, "error": f"write allowed only under {tuple(cfg.code.writable)}"}
    if candidate.is_dir():
        return {"ok": False, "error": "target is a directory"}
    if len(content) > WRITE_CAP:
        return {"ok": False, "error": "file too large for one write"}
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(content, encoding="utf-8")
    return {"ok": True, "path": posix, "bytes": len(content.encode("utf-8"))}


def read_product_file(relative_path: str) -> dict:
    """Read helper for the Coder scoped to product areas."""
    return read_repo_file(relative_path)


def run_security_scans_tool() -> dict:
    """Deterministic SAST/SCA/secrets evidence for the Security agent.

    Solo lectura; el agente interpreta la salida, jams la reemplaza con opinion.
    Herramienta ausente => TOOL_MISSING reportado honestamente (nunca simulado).

    Returns:
        {"scans": [...], "high": n, "blocked": bool, "degraded_scanners": [...]}.
    """
    from .scanners import run_security_scans

    return run_security_scans(work_root(), project().semgrep_targets())


_TEST_NAME = {
    "python": re.compile(r"^test_[\w-]+\.py$"),
    "javascript": re.compile(r"^[\w.-]+\.(?:test|spec)\.(?:js|jsx|mjs|cjs)$"),
    "typescript": re.compile(r"^[\w.-]+\.(?:test|spec)\.(?:ts|tsx|js|jsx)$"),
}


def is_test_file_name(name: str) -> bool:
    pattern = _TEST_NAME.get(project().config.code.language)
    return bool(pattern.match(name)) if pattern else True


def write_test_file(relative_path: str, content: str) -> dict:
    """Bounded write for the Test Analyst: only test files in the project's test area.

    Nunca codigo de aplicacion, requisitos, fabrica ni evidencia: delega en
    write_product_file para heredar TODAS sus denegaciones.

    Args:
        relative_path: Path inside code.tests with a test file name (python: test_*.py;
            javascript/typescript: *.test.* or *.spec.*).
        content: Full new file content.

    Returns:
        {"ok": True, "path": ..., "bytes": n} or {"ok": False, "error": "..."}.
    """
    tests_dir = project().config.code.tests
    root = work_root()
    candidate = (root / str(relative_path).replace("\\", "/")).resolve()
    try:
        posix = candidate.relative_to(root).as_posix()
    except ValueError:
        return {"ok": False, "error": "path escapes repository"}
    name = posix.rsplit("/", 1)[-1]
    if not posix.startswith(tests_dir + "/") or not is_test_file_name(name):
        return {"ok": False, "error": f"test analyst may write only test files under {tests_dir}/"}
    return write_product_file(relative_path, content)


# --- Reviewer/Security: diff de solo lectura (curso §8: inspect_git_diff) ---
def _git_root(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd or project().root, text=True, capture_output=True,
        encoding="utf-8", errors="replace", check=False,
    )


def _run_state(change_id: str) -> dict:
    path = project().run_dir(change_id) / "factory-state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _active_diff_range() -> str:
    """`<base_commit>...<rama del run>` del run activo. Sin run activo: el run cuya rama
    esta en HEAD (si tiene base guardada) o `<base_branch>...HEAD`."""
    p = project()
    cid = active_run()
    if cid:
        st = _run_state(cid)
        if st.get("base_commit"):
            return f"{st['base_commit']}...{st.get('branch') or p.branch(cid)}"
    head = _git_root(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    prefix = p.config.git.branch_prefix
    if head.startswith(prefix):
        try:
            base = _run_state(head[len(prefix):]).get("base_commit")
        except ValueError:
            base = None
        if base:
            return f"{base}...HEAD"
    return f"{p.config.git.base_branch}...HEAD"


def inspect_git_diff(stat_only: bool) -> dict:
    """Read-only: diff de la rama del run contra su base guardada. Sin argumentos libres.

    Args:
        stat_only: True for only the per-file summary (--stat), False for the full diff.

    Returns:
        {"ok": True, "diff": ...} (truncated at 40000 chars) or {"ok": False, "error": ...}.
    """
    cmd = ["diff", _active_diff_range()] + (["--stat"] if stat_only else [])
    completed = _git_root(cmd)
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr[-500:] or "git diff failed"}
    out = completed.stdout
    if len(out) > READ_CAP:
        out = out[:READ_CAP] + "\n...[TRUNCATED]..."
    return {"ok": True, "diff": out}


# --- Context engineering (curso §12): seleccionar, no volcar el repo ---
_CONTEXT_CAP = 12_000


def _sections(text: str, req_re: re.Pattern) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in re.split(r"(?m)^(?=## )", text):
        if not chunk.startswith("## "):
            continue
        heading = chunk.splitlines()[0][3:].strip()
        m = req_re.search(heading)
        out[m.group(0) if m else heading] = chunk.strip()
    return out


def _stems(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{5,}", text.lower())}


def _doc_text(rel: str | None) -> str:
    if not rel:
        return ""
    path = work_root() / rel
    if not path.is_file():
        path = project().root / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def select_context(change_id: str) -> dict:
    """Selecciona solo el contexto relevante para un Change Request y lo deja auditable.

    Descubre estructura via el repo map (si existe); elige secciones de requisitos/spec
    citadas por el CR (ids, nombre del CR, palabras clave), la arquitectura, los archivos
    fuente que el CR senala y los tests relacionados. Escribe
    <runs>/<CR>/context-manifest.json con lo incluido Y lo descartado.
    """
    p = project()
    cfg = p.config
    change_id = change_id.strip().upper()
    req_re, ac_re = re.compile(cfg.ids.requirement), re.compile(cfg.ids.acceptance)
    run_dir = p.run_dir(change_id)
    cr_path = run_dir / "00-change-request.md"
    if not cr_path.is_file():
        matches = sorted(p.cr_dir.glob(f"{change_id}-*.md"))
        cr_path = matches[0] if matches else cr_path
    cr_text = cr_path.read_text(encoding="utf-8") if cr_path.is_file() else ""
    cr_ids = set(req_re.findall(cr_text))
    cr_stems = _stems(cr_text)
    always = set(cfg.docs.always_include)

    def pick(rel: str | None) -> dict[str, str]:
        secs = _sections(_doc_text(rel), req_re)
        chosen = {}
        for key, body in secs.items():
            heading = body.splitlines()[0]
            if (
                key in cr_ids
                or key in always
                or change_id in body
                or (_stems(heading) & cr_stems)
            ):
                chosen[key] = body
        return chosen

    requirements, spec = pick(cfg.docs.requirements), pick(cfg.docs.spec)
    architecture = _doc_text(cfg.docs.architecture)

    root = work_root()
    repo_map_text = _doc_text(cfg.docs.repo_map)
    if repo_map_text:
        mapped = re.findall(r"\|\s*`([^`]+\.[A-Za-z0-9]+)`\s*\|", repo_map_text)
        discovery = cfg.docs.repo_map
    else:  # sin repo map: archivos reales de las areas de codigo
        mapped = [
            f.relative_to(root).as_posix()
            for src in cfg.code.source
            for f in sorted((root / src).rglob("*"))
            if f.is_file() and not any(part in SKIP_PARTS for part in f.parts)
        ][:200]
        discovery = "filesystem"
    product = [f for f in mapped if _under(f, cfg.code.source)]
    words = set(re.findall(r"[a-z]+", cr_text.lower()))

    def stem_of(path: str) -> str:
        return path.rsplit("/", 1)[-1].split(".", 1)[0]

    source = [f for f in product if stem_of(f).lower() in words]
    source_basis = "cr_mentions_module"
    if not source:  # el CR no senala modulo: producto completo segun el mapa (pequeno)
        source, source_basis = product, "repo_map_product_fallback"
    stems = {stem_of(f) for f in source}
    tests_dir = root / cfg.code.tests
    cr_acs = set(ac_re.findall(cr_text))
    tests = []
    if tests_dir.is_dir():
        for t in sorted(tests_dir.rglob("*")):
            if not t.is_file() or not is_test_file_name(t.name):
                continue
            body = t.read_text(encoding="utf-8", errors="replace")
            if any(s in body for s in stems) or cr_acs & set(ac_re.findall(body)):
                tests.append(t.relative_to(root).as_posix())

    text = "\n\n".join([*requirements.values(), *spec.values(), architecture])[:_CONTEXT_CAP]
    all_req = list(_sections(_doc_text(cfg.docs.requirements), req_re))
    all_spec = list(_sections(_doc_text(cfg.docs.spec), req_re))
    manifest = {
        "change_id": change_id,
        "change_request": cr_path.relative_to(p.root).as_posix() if cr_path.is_file() else None,
        "discovery": discovery,
        "included": {
            "requirements_sections": sorted(requirements),
            "spec_sections": sorted(spec),
            "architecture": bool(architecture),
            "source_files": source,
            "source_basis": source_basis,
            "tests": tests,
        },
        "excluded": {
            "requirements_sections": sorted(set(all_req) - set(requirements)),
            "spec_sections": sorted(set(all_spec) - set(spec)),
            "source_files": [f for f in product if f not in source],
            "areas": [
                *cfg.code.never_write, ".factory/", f"{cfg.paths.runs}/ (other runs)",
            ],
        },
        "text_chars": len(text),
        "whole_repo_sent": False,
    }
    if run_dir.is_dir():
        (run_dir / "context-manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
    return {"ok": True, "manifest": manifest, "text": text}
