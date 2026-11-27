from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
READABLE_ROOTS = {"docs", "change_requests", "campusflow", "tests", "runs"}


def _safe_repo_path(relative_path: str) -> Path:
    candidate = (REPO_ROOT / relative_path).resolve()
    if REPO_ROOT not in candidate.parents and candidate != REPO_ROOT:
        raise ValueError("path escapes repository")
    return candidate


def list_repo_files(prefix: str = "") -> dict:
    """List files under an allowlisted read-only area."""
    prefix = prefix.strip().strip("/")
    if prefix:
        top = prefix.split("/", 1)[0]
        if top not in READABLE_ROOTS:
            return {"ok": False, "error": f"read not allowed under {top!r}"}
        root = _safe_repo_path(prefix)
    else:
        root = REPO_ROOT
    if not root.exists():
        return {"ok": False, "error": "path does not exist"}
    items = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT)
        if not prefix and rel.parts[0] not in READABLE_ROOTS:
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        items.append(str(rel))
    return {"ok": True, "files": items[:250]}


def read_repo_file(relative_path: str) -> dict:
    """Read UTF-8 text from an allowlisted repository area."""
    path = _safe_repo_path(relative_path)
    rel = path.relative_to(REPO_ROOT)
    if not rel.parts or rel.parts[0] not in READABLE_ROOTS:
        return {"ok": False, "error": "read not allowed for this path"}
    if not path.is_file():
        return {"ok": False, "error": "file does not exist"}
    if path.suffix.lower() in {".db", ".png", ".jpg", ".jpeg", ".zip", ".pdf"}:
        return {"ok": False, "error": "binary file not supported"}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "file is not UTF-8 text"}
    if len(text) > 40_000:
        text = text[:40_000] + "\n...[TRUNCATED]..."
    return {"ok": True, "path": str(rel), "content": text}


def run_baseline_verification() -> dict:
    """Run an allowlisted deterministic baseline. No arbitrary shell input."""
    # sys.executable: el interprete del venv que corre la fabrica (un `python` del PATH
    # puede ser otro sin pytest/ruff y daria FAIL falsos).
    commands = [
        [sys.executable, "-m", "pytest", "-q"],
        [sys.executable, "-m", "ruff", "check", "campusflow", "tests", "factory_app", "scripts"],
    ]
    results = []
    all_ok = True
    for command in commands:
        label = " ".join(["python", *command[1:]])
        try:
            completed = subprocess.run(
                command, cwd=REPO_ROOT, text=True, capture_output=True,
                timeout=VERIFY_TIMEOUT, check=False,
            )
        except subprocess.TimeoutExpired as exc:  # FAIL registrado, no excepcion que tumbe el run
            all_ok = False
            results.append(
                {
                    "command": label,
                    "returncode": None,
                    "stdout": str(exc.stdout or "")[-6000:],
                    "stderr": f"timeout tras {VERIFY_TIMEOUT}s",
                }
            )
            continue
        all_ok = all_ok and completed.returncode == 0
        results.append(
            {
                "command": label,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-6000:],
                "stderr": completed.stderr[-3000:],
            }
        )
    return {"ok": all_ok, "results": results}


VERIFY_TIMEOUT = 120


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
    """Write only evidence files under runs/<CR-ID>/; never product code."""
    if not change_id.startswith("CR-"):
        return {"ok": False, "error": "change_id must start with CR-"}
    if "/" in filename or "\\" in filename or not filename.endswith((".md", ".json", ".txt")):
        return {"ok": False, "error": "invalid evidence filename"}
    if _is_engine_artifact(filename):
        return {
            "ok": False,
            "error": f"{filename!r} lo genera el motor o un humano; el agente no puede escribirlo",
        }
    folder = REPO_ROOT / "runs" / change_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(path.relative_to(REPO_ROOT))}


# --- T06: capacidad de escritura ACOTADA para el Coder (least privilege real) ---
WRITABLE_ROOTS = ("campusflow", "tests/product")
NEVER_WRITE = (
    ".env",
    "pyproject.toml",
    "AGENTS.md",
    "docs",
    "factory_app",
    "scripts",
    ".github",
    "change_requests",
    "runs",
)


def write_product_file(relative_path: str, content: str) -> dict:
    """Bounded write for the Coder: only product source and product tests.

    Denies requirements/spec/docs, the factory itself, evidence, secrets and
    configuration. El motor ademas registra intentos y corre verificacion
    determinista despues; el Coder nunca puede autoaprobarse.
    """
    candidate = (REPO_ROOT / relative_path).resolve()
    if REPO_ROOT not in candidate.parents:
        return {"ok": False, "error": "path escapes repository"}
    rel = candidate.relative_to(REPO_ROOT)
    top = rel.parts[0] if rel.parts else ""
    if top in NEVER_WRITE:
        return {"ok": False, "error": f"write denied for {top!r} (least privilege)"}
    posix = rel.as_posix()
    if not any(posix == w or posix.startswith(w + "/") for w in WRITABLE_ROOTS):
        return {"ok": False, "error": f"write allowed only under {WRITABLE_ROOTS}"}
    if candidate.is_dir():
        return {"ok": False, "error": "target is a directory"}
    if len(content) > 60_000:
        return {"ok": False, "error": "file too large for one write"}
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(rel), "bytes": len(content.encode("utf-8"))}


def read_product_file(relative_path: str) -> dict:
    """Read helper for the Coder scoped to product areas."""
    return read_repo_file(relative_path)


def run_security_scans_tool() -> dict:
    """Deterministic SAST/SCA/secrets evidence for the Security agent.

    Solo lectura; el agente interpreta la salida, jams la reemplaza con opinion.
    Herramienta ausente => TOOL_MISSING reportado honestamente (nunca simulado).
    """
    from .scanners import run_security_scans

    return run_security_scans(REPO_ROOT)


# --- Test Analyst: escritura SOLO de tests (curso §7: "write tests únicamente") ---
TEST_WRITABLE_ROOT = "tests/product/"


def write_test_file(relative_path: str, content: str) -> dict:
    """Bounded write for the Test Analyst: only tests/product/test_*.py.

    Nunca codigo de aplicacion (campusflow/), requisitos, fabrica ni evidencia:
    delega en write_product_file para heredar TODAS sus denegaciones.
    """
    candidate = (REPO_ROOT / relative_path).resolve()
    try:
        posix = candidate.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return {"ok": False, "error": "path escapes repository"}
    name = posix.rsplit("/", 1)[-1]
    if not posix.startswith(TEST_WRITABLE_ROOT) or not (
        name.startswith("test_") and name.endswith(".py")
    ):
        return {
            "ok": False,
            "error": f"test analyst may write only {TEST_WRITABLE_ROOT}test_*.py",
        }
    return write_product_file(relative_path, content)


# --- Reviewer/Security: diff de solo lectura (curso §8: inspect_git_diff) ---
def _active_diff_range() -> str:
    """`<base_commit>...HEAD` del run cuya rama factory/<CR> esta activa; si no hay run
    activo o no tiene base guardada, `main...HEAD`."""
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=REPO_ROOT, text=True, capture_output=True, check=False,
    ).stdout.strip()
    if head.startswith("factory/"):
        state_path = REPO_ROOT / "runs" / head.split("/", 1)[1] / "factory-state.json"
        if state_path.is_file():
            base = json.loads(state_path.read_text(encoding="utf-8")).get("base_commit")
            if base:
                return f"{base}...HEAD"
    return "main...HEAD"


def inspect_git_diff(stat_only: bool = False) -> dict:
    """Read-only: diff de la rama del run contra su base guardada. Sin argumentos libres."""
    cmd = ["git", "diff", _active_diff_range()] + (["--stat"] if stat_only else [])
    completed = subprocess.run(
        cmd, cwd=REPO_ROOT, text=True, capture_output=True, timeout=60, check=False
    )
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr[-500:] or "git diff failed"}
    out = completed.stdout
    if len(out) > 40_000:
        out = out[:40_000] + "\n...[TRUNCATED]..."
    return {"ok": True, "diff": out}


# --- Context engineering (curso §12): seleccionar, no volcar el repo ---
_AC = re.compile(r"AC-[A-Za-z0-9]+\.\d+")
_REQ_ID = re.compile(r"\b(?:N?FR)-\d+\b")
_CONTEXT_CAP = 12_000
_ALWAYS = {"NFR-01"}  # restriccion arquitectonica aplica a todo cambio


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in re.split(r"(?m)^(?=## )", text):
        if not chunk.startswith("## "):
            continue
        heading = chunk.splitlines()[0][3:].strip()
        m = _REQ_ID.search(heading)
        out[m.group(0) if m else heading] = chunk.strip()
    return out


def _stems(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{5,}", text.lower())}


def select_context(change_id: str) -> dict:
    """Selecciona solo el contexto relevante para un Change Request y lo deja auditable.

    Descubre estructura via docs/REPO_MAP.md; elige secciones de REQUIREMENTS/SPEC
    citadas por el CR (ids FR/NFR, nombre del CR, palabras clave), ARCHITECTURE, los
    archivos fuente que el CR/REPO_MAP senalan y los tests relacionados. Escribe
    runs/<CR>/context-manifest.json con lo incluido Y lo descartado.
    """
    change_id = change_id.strip().upper()
    run_dir = REPO_ROOT / "runs" / change_id
    cr_path = run_dir / "00-change-request.md"
    if not cr_path.is_file():
        matches = sorted((REPO_ROOT / "change_requests").glob(f"{change_id}-*.md"))
        cr_path = matches[0] if matches else cr_path
    cr_text = cr_path.read_text(encoding="utf-8") if cr_path.is_file() else ""
    cr_ids = set(_REQ_ID.findall(cr_text))
    cr_stems = _stems(cr_text)

    def pick(doc: str) -> dict[str, str]:
        path = REPO_ROOT / "docs" / doc
        secs = _sections(path.read_text(encoding="utf-8")) if path.is_file() else {}
        chosen = {}
        for key, body in secs.items():
            heading = body.splitlines()[0]
            if (
                key in cr_ids
                or key in _ALWAYS
                or change_id in body
                or (_stems(heading) & cr_stems)
            ):
                chosen[key] = body
        return chosen

    requirements, spec = pick("REQUIREMENTS.md"), pick("SPEC.md")
    arch_path = REPO_ROOT / "docs" / "ARCHITECTURE.md"
    architecture = arch_path.read_text(encoding="utf-8") if arch_path.is_file() else ""

    repo_map = REPO_ROOT / "docs" / "REPO_MAP.md"
    mapped = (
        re.findall(r"\|\s*`([^`]+\.py)`\s*\|", repo_map.read_text(encoding="utf-8"))
        if repo_map.is_file()
        else []
    )
    product = [p for p in mapped if p.startswith("campusflow/")]
    words = set(re.findall(r"[a-z]+", cr_text.lower()))
    source = [p for p in product if p.rsplit("/", 1)[-1][:-3] in words]
    source_basis = "cr_mentions_module"
    if not source:  # el CR no senala modulo: producto completo segun REPO_MAP (pequeno)
        source, source_basis = product, "repo_map_product_fallback"
    stems = {p.rsplit("/", 1)[-1][:-3] for p in source}
    tests_dir = REPO_ROOT / "tests" / "product"
    tests = [
        t.relative_to(REPO_ROOT).as_posix()
        for t in sorted(tests_dir.glob("test_*.py"))
        if tests_dir.is_dir()
        and (
            any(s in t.read_text(encoding="utf-8") for s in stems)
            or set(_AC.findall(cr_text)) & set(_AC.findall(t.read_text(encoding="utf-8")))
        )
    ]

    text = "\n\n".join([*requirements.values(), *spec.values(), architecture])[:_CONTEXT_CAP]
    manifest = {
        "change_id": change_id,
        "change_request": cr_path.relative_to(REPO_ROOT).as_posix() if cr_path.is_file() else None,
        "discovery": "docs/REPO_MAP.md",
        "included": {
            "requirements_sections": sorted(requirements),
            "spec_sections": sorted(spec),
            "architecture": bool(architecture),
            "source_files": source,
            "source_basis": source_basis,
            "tests": tests,
        },
        "excluded": {
            "requirements_sections": sorted(
                set(_sections_ids("REQUIREMENTS.md")) - set(requirements)
            ),
            "spec_sections": sorted(set(_sections_ids("SPEC.md")) - set(spec)),
            "source_files": [p for p in product if p not in source],
            "areas": ["factory_app/", "scripts/", ".github/", "runs/ (other runs)", "uv.lock"],
        },
        "text_chars": len(text),
        "whole_repo_sent": False,
    }
    if run_dir.is_dir():
        (run_dir / "context-manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
    return {"ok": True, "manifest": manifest, "text": text}


def _sections_ids(doc: str) -> list[str]:
    path = REPO_ROOT / "docs" / doc
    return list(_sections(path.read_text(encoding="utf-8"))) if path.is_file() else []
