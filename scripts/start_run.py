from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CR_DIR = ROOT / "change_requests"
RUNS = ROOT / "runs"


def find_change_request(change_id: str) -> Path:
    matches = sorted(CR_DIR.glob(f"{change_id}-*.md"))
    if len(matches) != 1:
        raise SystemExit(f"Expected one Change Request for {change_id}; found {len(matches)}")
    return matches[0]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/start_run.py CR-001")
    change_id = sys.argv[1].strip().upper()
    source = find_change_request(change_id)
    run = RUNS / change_id
    if run.exists():
        raise SystemExit(f"Run already exists: {run}")
    run.mkdir(parents=True)
    shutil.copy2(source, run / "00-change-request.md")
    state = {
        "change_id": change_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "INTAKE_PENDING",
        "human_gates": {
            "requirements": "PENDING",
            "plan": "PENDING",
            "review": "PENDING",
            "release": "PENDING",
        },
        # mismo shape que escribe el motor (factory-run.json nunca mezcla str y dict)
        "verification": {"status": "NOT_RUN", "tests": 0, "failed": 0, "lint": "NOT_RUN"},
        "review": {"status": "NOT_RUN", "must_fix": 0, "should_fix": 0},
        "security": {"status": "NOT_RUN", "high": 0, "medium": 0},
        "iterations": 0,
    }
    (run / "factory-state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Created {run.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
