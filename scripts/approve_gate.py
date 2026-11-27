"""Usage: python scripts/approve_gate.py CR-001 plan APPROVED "reason" --by "Name (Role)"
[--project RUTA]"""

from __future__ import annotations

import os
import sys

from _cli import pop_project, run

from factory_app import services


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
    project, argv = pop_project(sys.argv[1:])
    change_id, gate, decision, reason, recorded_by = parse_args(argv)
    if "TODO" in recorded_by:
        raise SystemExit('Identifique al humano: --by "Nombre (Rol)" (o export HUMAN_ROLE)')
    run(services.approve_gate, project, change_id, gate, decision, reason, recorded_by)
    path = project.run_dir(change_id) / f"human-{gate}-decision.json"
    print(
        f"Recorded {gate}: {decision} by {recorded_by} -> "
        f"{path.relative_to(project.root).as_posix()}"
    )


if __name__ == "__main__":
    main()
