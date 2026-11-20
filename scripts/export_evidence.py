"""Exporta la evidencia de un run completado a evidence/<CR-ID>/ (siempre commiteable).

runs/ es area de trabajo (gitignoreada, puede contener borradores/rounds);
evidence/ es el registro auditable del curso: cada equipo commitea la evidencia de
sus 3 runs minimos + el CR sorpresa de la defensa.

Usage: python scripts/export_evidence.py CR-001
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = (
    "00-change-request.md",
    "factory-run.json",
    "traceability.md",
    "14-evidence-summary.md",
)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/export_evidence.py CR-001")
    change_id = sys.argv[1].upper()
    run = ROOT / "runs" / change_id
    state_path = run / "factory-state.json"
    if not state_path.is_file():
        raise SystemExit(f"Run not found: {change_id}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    missing = [a for a in REQUIRED if not (run / a).is_file()]
    if missing:
        raise SystemExit(
            f"Evidencia incompleta para exportar: faltan {missing} (estado: {state['status']})"
        )
    if state["status"] not in {"READY FOR HUMAN MERGE", "NOT READY", "ESCALATED"}:
        raise SystemExit(
            f"El run aun no termino (status={state['status']}). "
            "Exporta solo tras el release gate o un escalamiento registrado."
        )
    dest = ROOT / "evidence" / change_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    exported = []
    for f in sorted(run.iterdir()):
        if f.is_file():
            shutil.copy2(f, dest / f.name)
            exported.append(f.name)
    print(f"Exportados {len(exported)} artefactos -> {dest.relative_to(ROOT)}")
    print("Commitea la evidencia: git add evidence/ && git commit -m 'evidence: " + change_id + "'")


if __name__ == "__main__":
    main()
