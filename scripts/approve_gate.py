from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _roles import validate_role  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VALID_GATES = {"requirements", "plan", "review", "release"}
VALID_DECISIONS = {"APPROVED", "MODIFIED", "REJECTED", "DEFERRED"}


def parse_args(argv: list[str]) -> tuple[str, str, str, str, str]:
    if "--by" not in argv:
        argv = argv + ["--by", os.environ.get("HUMAN_ROLE", "TODO: set --by 'Name (Role)'")]
    by_index = argv.index("--by")
    if by_index + 1 >= len(argv):
        raise SystemExit('--by requires a value: --by "Hector (Tech Lead)"')
    recorded_by = argv[by_index + 1]
    rest = argv[:by_index] + argv[by_index + 2 :]
    if len(rest) < 4:
        raise SystemExit(
            'Usage: python scripts/approve_gate.py CR-001 plan APPROVED "reason" --by "Name (Role)"'
        )
    change_id, gate, decision = rest[0].upper(), rest[1].lower(), rest[2].upper()
    reason = " ".join(rest[3:]).strip()
    return change_id, gate, decision, reason, recorded_by


def main() -> None:
    change_id, gate, decision, reason, recorded_by = parse_args(sys.argv[1:])
    if gate not in VALID_GATES:
        raise SystemExit(f"Invalid gate: {sorted(VALID_GATES)}")
    if decision not in VALID_DECISIONS:
        raise SystemExit(f"Invalid decision: {sorted(VALID_DECISIONS)}")
    if not reason:
        raise SystemExit("Human decision requires a reason")
    if "TODO" in recorded_by:
        raise SystemExit('Identifique al humano: --by "Nombre (Rol)" (o export HUMAN_ROLE)')
    role = validate_role(gate, recorded_by)
    run = ROOT / "runs" / change_id
    state_path = run / "factory-state.json"
    if not state_path.exists():
        raise SystemExit(f"Run not found: {change_id}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["human_gates"][gate] = decision
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    evidence = {
        "change_id": change_id,
        "gate": gate,
        "decision": decision,
        "reason": reason,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "recorded_by": recorded_by,
        "role": role,
        "note": "Decision humana explícita; la fábrica no interpreta silencio.",
    }
    path = run / f"human-{gate}-decision.json"
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"Recorded {gate}: {decision} by {recorded_by} -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
