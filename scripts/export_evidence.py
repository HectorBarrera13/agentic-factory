"""Exporta la evidencia de un run terminado a <paths.evidence>/<CR-ID>/ (commiteable).

runs/ es area de trabajo (gitignoreada, puede contener borradores/rounds);
la evidencia exportada es el registro auditable del curso.

Usage: python scripts/export_evidence.py CR-001 [--project RUTA]
"""

from __future__ import annotations

import sys

from _cli import pop_project, run

from factory_app import services


def main() -> None:
    project, argv = pop_project(sys.argv[1:])
    if len(argv) != 1:
        raise SystemExit("Usage: python scripts/export_evidence.py CR-001 [--project RUTA]")
    out = run(services.export_evidence, project, argv[0])
    print(f"Exportados {len(out['files'])} artefactos -> {out['dest']}")
    print(f"Commitea la evidencia: git add {out['dest']} && git commit -m 'evidence: {argv[0]}'")


if __name__ == "__main__":
    main()
