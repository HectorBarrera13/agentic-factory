from __future__ import annotations

import subprocess
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
        if any(part.startswith(".") for part in rel.parts):
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
    text = path.read_text(encoding="utf-8")
    if len(text) > 40_000:
        text = text[:40_000] + "\n...[TRUNCATED]..."
    return {"ok": True, "path": str(rel), "content": text}


def run_baseline_verification() -> dict:
    """Run an allowlisted deterministic baseline. No arbitrary shell input."""
    commands = [
        ["python", "-m", "pytest", "-q"],
        ["python", "-m", "ruff", "check", "campusflow", "tests", "factory_app", "scripts"],
    ]
    results = []
    all_ok = True
    for command in commands:
        completed = subprocess.run(
            command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=120, check=False
        )
        all_ok = all_ok and completed.returncode == 0
        results.append(
            {
                "command": " ".join(command),
                "returncode": completed.returncode,
                "stdout": completed.stdout[-6000:],
                "stderr": completed.stderr[-3000:],
            }
        )
    return {"ok": all_ok, "results": results}


def write_run_evidence(change_id: str, filename: str, content: str) -> dict:
    """Write only evidence files under runs/<CR-ID>/; never product code."""
    if not change_id.startswith("CR-"):
        return {"ok": False, "error": "change_id must start with CR-"}
    if "/" in filename or "\\" in filename or not filename.endswith((".md", ".json", ".txt")):
        return {"ok": False, "error": "invalid evidence filename"}
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
