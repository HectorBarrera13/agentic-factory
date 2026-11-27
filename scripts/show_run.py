"""Usage: python scripts/show_run.py CR-001 [--project RUTA]"""

from __future__ import annotations

import json
import sys

from _cli import pop_project, run

from factory_app import services


def main() -> None:
    project, argv = pop_project(sys.argv[1:])
    if len(argv) != 1:
        raise SystemExit("Usage: python scripts/show_run.py CR-001 [--project RUTA]")
    print(json.dumps(run(services.read_state, project, argv[0]), indent=2))


if __name__ == "__main__":
    main()
