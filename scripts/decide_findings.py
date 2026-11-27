"""Gate 2: clasificacion HUMANA de cada finding (ACCEPTED/REJECTED/MODIFIED/DEFERRED).

Usage:
  python scripts/decide_findings.py CR-001 --by "Ana (Human Reviewer)" \
      RF-01=ACCEPTED RF-02=DEFERRED SEC-01=MODIFIED SEC-SCAN-01=REJECTED [--project RUTA]

Solo los findings ACCEPTED/MODIFIED autorizan correcciones al Coder. Sin este
registro, LA FABRICA NO APLICA NADA (los agentes no deciden que cambia el codigo).
Varias llamadas en la misma ronda se ACUMULAN (la ultima decision por finding gana).
"""

from __future__ import annotations

import sys

from _cli import pop_project, run

from factory_app import services
from factory_app.services import report_finding_ids

__all__ = ["main", "report_finding_ids"]
USAGE = 'Usage: python scripts/decide_findings.py CR-001 --by "Name (Role)" RF-01=ACCEPTED ...'


def main() -> None:
    project, argv = pop_project(sys.argv[1:])
    if len(argv) < 4 or "--by" not in argv:
        raise SystemExit(USAGE)
    change_id = argv[0].upper()
    by_index = argv.index("--by")
    if by_index + 1 >= len(argv) or "=" in argv[by_index + 1]:
        raise SystemExit('--by requiere un valor: --by "Ana (Human Reviewer)"')
    recorded_by = argv[by_index + 1]
    pairs = [a for a in argv[1:by_index] + argv[by_index + 2 :] if "=" in a]
    if not pairs:
        raise SystemExit("Include at least one FINDING=VERDICT pair")
    verdicts: dict[str, str] = {}
    for pair in pairs:
        fid, _, verdict = pair.partition("=")
        verdicts[fid] = verdict
    out = run(services.decide_findings, project, change_id, verdicts, recorded_by)
    print(f"Decisiones por finding: {len(out['decisions'])} -> {out['path']}")
    if out["pending"]:
        print(f"Pendientes de clasificar: {', '.join(out['pending'])}")
    else:
        print(
            "Todos clasificados. Cierra el gate con: python scripts/approve_gate.py "
            f'{change_id} review APPROVED "findings clasificados" --by "{recorded_by}"'
        )


if __name__ == "__main__":
    main()
