"""Gate 2: clasificacion HUMANA de cada finding (ACCEPTED/REJECTED/MODIFIED/DEFERRED).

Usage:
  python scripts/decide_findings.py CR-001 --by "Ana (Human Reviewer)" \
      RF-01=ACCEPTED RF-02=DEFERRED SEC-01=MODIFIED

Solo los findings ACCEPTED/MODIFIED autorizan correcciones al Coder. Sin este
registro, LA FABRICA NO APLICA NADA (los agentes no deciden que cambia el codigo).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _roles import validate_role  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VERDICTS = {"ACCEPTED", "REJECTED", "MODIFIED", "DEFERRED"}
FINDING_RE = re.compile(r"^(RF|SEC)-\d{2,}$")


def main() -> None:
    argv = sys.argv[1:]
    if len(argv) < 4 or "--by" not in argv:
        raise SystemExit(
            'Usage: python scripts/decide_findings.py CR-001 --by "Name (Role)" RF-01=ACCEPTED ...'
        )
    change_id = argv[0].upper()
    by_index = argv.index("--by")
    recorded_by = argv[by_index + 1]
    role = validate_role("findings", recorded_by)
    pairs = [a for i, a in enumerate(argv[by_index + 2 :], start=by_index + 2) if "=" in a]
    if not pairs:
        raise SystemExit("Include at least one FINDING=VERDICT pair")

    decisions = []
    for pair in pairs:
        fid, _, verdict = pair.partition("=")
        fid, verdict = fid.strip(), verdict.strip().upper()
        if not FINDING_RE.match(fid):
            raise SystemExit(f"finding id invalido: {fid} (usa RF-## o SEC-##)")
        if verdict not in VERDICTS:
            raise SystemExit(f"verdict invalido para {fid}: {verdict} -> {sorted(VERDICTS)}")
        decisions.append({"finding_id": fid, "decision": verdict})

    run = ROOT / "runs" / change_id
    if not (run / "factory-state.json").is_file():
        raise SystemExit(f"Run not found: {change_id}")
    reports = ""
    for name in ("08-review-report.md", "09-security-report.md"):
        if (run / name).is_file():
            reports += (run / name).read_text(encoding="utf-8")
    known = set(re.findall(r"((?:RF|SEC)-\d{2,})", reports))
    unknown = [d["finding_id"] for d in decisions if known and d["finding_id"] not in known]
    if unknown:
        raise SystemExit(f"findings no presentes en los reportes del run: {unknown}")

    payload = {
        "change_id": change_id,
        "gate": "findings",
        "decisions": decisions,
        "recorded_by": recorded_by,
        "role": role,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "note": "Los ACCEPTED/MODIFIED bloqueantes autorizan Coder Fix; el resto no cambia codigo.",
    }
    path = run / "human-findings-decisions.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # el gate global de review queda pendiente hasta approve_gate review (clasificacion + cierre)
    print(f"Recordadas {len(decisions)} decisiones por finding -> {path.relative_to(ROOT)}")
    print(
        "Cierra el gate con: python scripts/approve_gate.py "
        f'{change_id} review APPROVED "findings clasificados" '
        f'--by "{recorded_by or os.environ.get("HUMAN_ROLE", "")}"'
    )


if __name__ == "__main__":
    main()
