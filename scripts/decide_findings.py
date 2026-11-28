"""Gate 2: clasificacion HUMANA de cada finding (ACCEPTED/REJECTED/MODIFIED/DEFERRED).

Usage:
  python scripts/decide_findings.py CR-001 --by "Ana (Human Reviewer)" \
      RF-01=ACCEPTED RF-02=ACCEPTED:blocking SEC-01=MODIFIED SEC-SCAN-01=REJECTED \
      [--reason RF-02="falta llamar a resolveConflicts"] [--project RUTA]

Solo los findings ACCEPTED/MODIFIED bloqueantes autorizan correcciones al Coder: MUST FIX /
HIGH, o los marcados `:blocking` ("corregir en este ciclo"). `--reason ID=texto` guarda el
motivo de ese finding (llega al Coder Fix). Sin este registro, LA FABRICA NO APLICA NADA
(los agentes no deciden que cambia el codigo).
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
    rest = argv[1:by_index] + argv[by_index + 2 :]
    reasons: dict[str, str] = {}
    pairs: list[str] = []
    i = 0
    while i < len(rest):
        if rest[i] == "--reason":
            if i + 1 >= len(rest) or "=" not in rest[i + 1]:
                raise SystemExit('--reason requiere ID=texto: --reason RF-01="motivo"')
            fid, _, text = rest[i + 1].partition("=")
            reasons[fid.strip().upper()] = text
            i += 2
            continue
        if "=" in rest[i]:
            pairs.append(rest[i])
        i += 1
    if not pairs:
        raise SystemExit("Include at least one FINDING=VERDICT pair")
    verdicts: dict[str, object] = {}
    for pair in pairs:
        fid, _, verdict = pair.partition("=")
        decision, _, flag = verdict.partition(":")
        fid = fid.strip().upper()
        if flag and flag.strip().lower() != "blocking":
            raise SystemExit(f"marca desconocida en {pair}: usa ID=VERDICT o ID=VERDICT:blocking")
        verdicts[fid] = (
            {"decision": decision, "reason": reasons.get(fid, ""), "blocking": bool(flag)}
            if flag or fid in reasons
            else decision
        )
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
