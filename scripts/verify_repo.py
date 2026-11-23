from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = [
    [sys.executable, "-m", "pytest", "-q"],
    [sys.executable, "-m", "ruff", "check", "campusflow", "tests", "factory_app", "scripts"],
]


REQUIRED_FILES = (
    "AGENTS.md",
    "docs/REQUIREMENTS.md",
    "docs/SPEC.md",
    "docs/ARCHITECTURE.md",
    "docs/REPO_MAP.md",
    "factory_app/contracts.py",
    "scripts/approve_gate.py",
    "scripts/decide_findings.py",
)


def repository_contract() -> list[str]:
    """Repo checks deterministas (curso §15): artefactos obligatorios y .env sin trackear."""
    problems = [f"missing required file: {p}" for p in REQUIRED_FILES if not (ROOT / p).is_file()]
    tracked = subprocess.run(
        ["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if tracked.stdout.strip():
        problems.append(".env is tracked by git (secret must never be committed)")
    return problems


def main() -> int:
    failed = False
    for command in COMMANDS:
        print(f"\n$ {' '.join(command)}")
        result = subprocess.run(command, cwd=ROOT, check=False)
        failed = failed or result.returncode != 0
    problems = repository_contract()
    print("\n$ repository contract:", "OK" if not problems else "")
    for problem in problems:
        print(f"  - {problem}")
    failed = failed or bool(problems)
    print("\nVERIFY_REPO:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
