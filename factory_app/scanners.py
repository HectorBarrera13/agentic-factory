"""Escáneres deterministas de seguridad (gate SAST/SCA/secrets §15 del curso).

Diseño: si la herramienta no está instalada, el resultado es DEGRADADO (status
TOOL_MISSING) y `blocked=False`, pero el reporte lo dice explicitamente — la fabrica
jamás simula evidencia. En CI (con extras) corren reales.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

SEVERITY_HIGH = {"HIGH", "CRITICAL", "ERROR"}


def _run(cmd: list[str], cwd: Path, timeout: int = 300) -> dict:
    if shutil.which(cmd[0]) is None:
        return {
            "tool": cmd[0],
            "status": "TOOL_MISSING",
            "returncode": None,
            "stdout": "",
            "stderr": "",
        }
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return {
            "tool": cmd[0],
            "status": "OK",
            "returncode": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "tool": cmd[0],
            "status": "TIMEOUT",
            "returncode": None,
            "stdout": "",
            "stderr": "timeout",
        }


def semgrep_scan(root: Path) -> dict:
    r = _run(["semgrep", "scan", "--config", "auto", "--json", "campusflow"], root, timeout=420)
    if r["status"] != "OK":
        return {"scanner": "semgrep", **{k: r[k] for k in ("status", "stderr")}, "findings": []}
    findings = []
    try:
        data = json.loads(r["stdout"] or "{}")
        for item in data.get("results", []):
            sev = str(item.get("extra", {}).get("severity", "INFO")).upper()
            findings.append(
                {
                    "rule": item.get("check_id"),
                    "severity": "HIGH"
                    if sev in SEVERITY_HIGH
                    else "MEDIUM"
                    if sev == "WARNING"
                    else "LOW",
                    "file": item.get("path"),
                    "line": item.get("start", {}).get("line"),
                }
            )
    except json.JSONDecodeError:
        findings = [{"rule": "parse_error", "severity": "LOW", "file": "-", "line": 0}]
    return {"scanner": "semgrep", "status": "OK", "findings": findings}


def pip_audit_scan(root: Path) -> dict:
    req = root / "requirements.txt"
    target = (
        ["pip-audit", "--format", "json", "-r", "requirements.txt"]
        if req.is_file()
        else ["pip-audit", "--format", "json"]
    )
    r = _run(target, root)
    if r["status"] != "OK":
        return {"scanner": "pip-audit", "status": r["status"], "findings": []}
    findings = []
    try:
        data = json.loads(r["stdout"] or "{}")
        for dep in data.get("dependencies", []):
            for vuln in dep.get("vulns", []):
                findings.append(
                    {
                        "rule": vuln.get("id"),
                        "severity": "HIGH",
                        "file": dep.get("name"),
                        "line": None,
                    }
                )
    except json.JSONDecodeError:
        pass
    return {"scanner": "pip-audit", "status": "OK", "findings": findings}


def secret_scan(root: Path) -> dict:
    r = _run(["detect-secrets", "scan"], root)
    if r["status"] != "OK":
        # fallback determinista simple incluso sin la herramienta
        pattern = re.compile(
            r"(AIza[0-9A-Za-z_\-]{35}|AQ\.[A-Za-z0-9_\-]{30,}|sk-[0-9A-Za-z]{20,}"
            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
        )
        hits = []
        for f in root.rglob("*"):
            if (
                f.is_file()
                and f.suffix in {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt"}
                and ".venv" not in f.parts
            ):
                try:
                    for i, line in enumerate(
                        f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                    ):
                        if pattern.search(line):
                            hits.append(
                                {
                                    "rule": "secret-pattern",
                                    "severity": "HIGH",
                                    "file": str(f.relative_to(root)),
                                    "line": i,
                                }
                            )
                except OSError:
                    continue
        return {
            "scanner": "secrets",
            "status": "FALLBACK_REGEX" if r["status"] == "TOOL_MISSING" else r["status"],
            "findings": hits,
        }
    data = json.loads(r["stdout"] or "{}")
    findings = [
        {"rule": s.get("type", "?"), "severity": "HIGH", "file": f, "line": s.get("line_number")}
        for f, secrets in data.get("results", {}).items()
        for s in secrets
    ]
    return {"scanner": "secrets", "status": "OK", "findings": findings}


def run_security_scans(root: Path) -> dict:
    scans = [semgrep_scan(root), pip_audit_scan(root), secret_scan(root)]
    high = [f for scan in scans for f in scan.get("findings", []) if f.get("severity") == "HIGH"]
    degraded = [scan["scanner"] for scan in scans if scan["status"] != "OK"]
    return {"scans": scans, "high": len(high), "blocked": bool(high), "degraded_scanners": degraded}
