"""Usage: python scripts/retry_run.py CR-001 "motivo" --by "Name (Product Owner)" [--force]
[--project RUTA]

Nuevo intento del MISMO CR tras ESCALATED / NOT READY. Edita primero el archivo del CR en
la carpeta de change requests: su contenido actual es el del nuevo intento. El intento
anterior se archiva en runs/<CR>/intentos/<n>/ y su rama se conserva como
factory/<CR>-intento<n>. --force solo si ya se alcanzo el tope de intentos.
"""

from __future__ import annotations

import os
import sys

from _cli import pop_project, run

from factory_app import services


def parse_args(argv: list[str]) -> tuple[str, str, str, bool]:
    force = "--force" in argv
    argv = [a for a in argv if a != "--force"]
    if "--by" not in argv:
        argv = argv + ["--by", os.environ.get("HUMAN_ROLE", "TODO: set --by 'Name (Role)'")]
    by_index = argv.index("--by")
    if by_index + 1 >= len(argv):
        raise SystemExit('--by requires a value: --by "Ana (Product Owner)"')
    recorded_by = argv[by_index + 1]
    rest = argv[:by_index] + argv[by_index + 2 :]
    if len(rest) < 2:
        raise SystemExit(
            'Usage: python scripts/retry_run.py CR-001 "motivo" --by "Name (Product Owner)"'
        )
    return rest[0].upper(), " ".join(rest[1:]).strip(), recorded_by, force


def main() -> None:
    project, argv = pop_project(sys.argv[1:])
    change_id, reason, recorded_by, force = parse_args(argv)
    if "TODO" in recorded_by:
        raise SystemExit('Identifique al humano: --by "Nombre (Rol)" (o export HUMAN_ROLE)')
    body = run(services.find_change_request, project, change_id).read_text(encoding="utf-8")
    state = run(services.retry_run, project, change_id, body, reason, recorded_by, force)
    last = state["previous_attempts"][-1]
    print(
        f"{change_id}: intento {state['attempt']} en INTAKE_PENDING. "
        f"Anterior archivado en {last['archive']}"
        + (f"; rama conservada {last['branch']}" if last.get("branch") else "")
    )


if __name__ == "__main__":
    main()
