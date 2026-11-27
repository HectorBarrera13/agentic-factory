"""Usage: python scripts/start_run.py CR-001 [--project RUTA]"""

from __future__ import annotations

import sys

from _cli import pop_project, run

from factory_app import services


def main() -> None:
    project, argv = pop_project(sys.argv[1:])
    if len(argv) != 1:
        raise SystemExit("Usage: python scripts/start_run.py CR-001 [--project RUTA]")
    state = run(services.start_run, project, argv[0])
    rel = project.run_dir(state["change_id"]).relative_to(project.root).as_posix()
    print(f"Created {rel}")


if __name__ == "__main__":
    main()
