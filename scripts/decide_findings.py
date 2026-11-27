"""Gate 2: clasificacion HUMANA de cada finding (ACCEPTED/REJECTED/MODIFIED/DEFERRED).

Usage:
  python scripts/decide_findings.py CR-001 --by "Ana (Human Reviewer)" \
      RF-01=ACCEPTED RF-02=DEFERRED SEC-01=MODIFIED SEC-SCAN-01=REJECTED

Solo los findings ACCEPTED/MODIFIED autorizan correcciones al Coder. Sin este
registro, LA FABRICA NO APLICA NADA (los agentes no deciden que cambia el codigo).
Varias llamadas en la misma ronda se ACUMULAN (la ultima decision por finding gana).
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
FINDING_RE = re.compile(r"^(RF|SEC|SEC-SCAN)-\d{2,}$")
REPORT_ID_RE = re.compile(r"\b(?:RF|SEC(?:-SCAN)?)-\d{2,}\b")
USAGE = 'Usage: python scripts/decide_findings.py CR-001 --by "Name (Role)" RF-01=ACCEPTED ...'


def report_finding_ids(run: Path) -> set[str]:
    """IDs de finding presentes en los reportes de la ronda actual."""
    text = ""
    for name in ("08-review-report.md", "09-security-report.md"):
        if (run / name).is_file():
            text += (run / name).read_text(encoding="utf-8") + "\n"
    return set(REPORT_ID_RE.findall(text))


def main() -> None:
    argv = sys.argv[1:]
    if len(argv) < 4 or "--by" not in argv:
        raise SystemExit(USAGE)
    change_id = argv[0].upper()
    by_index = argv.index("--by")
    if by_index + 1 >= len(argv) or "=" in argv[by_index + 1]:
        raise SystemExit('--by requiere un valor: --by "Ana (Human Reviewer)"')
    recorded_by = argv[by_index + 1]
    role = validate_role("findings", recorded_by)
    pairs = [a for a in argv[1:by_index] + argv[by_index + 2 :] if "=" in a]
    if not pairs:
        raise SystemExit("Include at least one FINDING=VERDICT pair")

    decisions = []
    for pair in pairs:
        fid, _, verdict = pair.partition("=")
        fid, verdict = fid.strip().upper(), verdict.strip().upper()
        if not FINDING_RE.match(fid):
            raise SystemExit(f"finding id invalido: {fid} (usa RF-##, SEC-## o SEC-SCAN-##)")
        if verdict not in VERDICTS:
            raise SystemExit(f"verdict invalido para {fid}: {verdict} -> {sorted(VERDICTS)}")
        decisions.append({"finding_id": fid, "decision": verdict})

    run = ROOT / "runs" / change_id
    state_path = run / "factory-state.json"
    if not state_path.is_file():
        raise SystemExit(f"Run not found: {change_id}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("awaiting_gate") != "review":
        raise SystemExit(
            f"La fabrica no esta esperando el gate review en {change_id} "
            f"(status={state.get('status')}); los findings se clasifican cuando el run se "
            "detiene en review."
        )
    known = report_finding_ids(run)
    unknown = [d["finding_id"] for d in decisions if known and d["finding_id"] not in known]
    if unknown:
        raise SystemExit(f"findings no presentes en los reportes del run: {unknown}")

    path = run / "human-findings-decisions.json"
    merged: dict[str, dict] = {}
    if path.is_file():  # misma ronda: se acumula (las rondas previas ya fueron archivadas)
        for d in json.loads(path.read_text(encoding="utf-8")).get("decisions", []):
            merged[d["finding_id"]] = d
    for d in decisions:
        merged[d["finding_id"]] = d
    payload = {
        "change_id": change_id,
        "gate": "findings",
        "decisions": sorted(merged.values(), key=lambda d: d["finding_id"]),
        "recorded_by": recorded_by,
        "role": role,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "note": "Los ACCEPTED/MODIFIED bloqueantes autorizan Coder Fix; el resto no cambia codigo.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    pending = sorted(known - set(merged))
    print(f"Decisiones por finding: {len(merged)} -> {path.relative_to(ROOT)}")
    if pending:
        print(f"Pendientes de clasificar: {', '.join(pending)}")
    else:
        print(
            "Todos clasificados. Cierra el gate con: python scripts/approve_gate.py "
            f'{change_id} review APPROVED "findings clasificados" '
            f'--by "{recorded_by or os.environ.get("HUMAN_ROLE", "")}"'
        )


if __name__ == "__main__":
    main()
