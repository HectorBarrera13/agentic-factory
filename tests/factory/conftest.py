"""Los tests de la fabrica no dependen de que semgrep/pip-audit/detect-secrets esten
instalados ni de la red: por defecto los escaneres externos se ven como TOOL_MISSING
(semgrep --config auto descarga reglas en cada review y multiplicaba x6 la duracion de la
suite, rompiendo el timeout de la verificacion de la propia fabrica).

Los tests de escaneres que necesitan el `_run` real se marcan con `real_scanner_run`.
"""

from __future__ import annotations

import pytest

from factory_app import scanners


def pytest_configure(config):
    config.addinivalue_line("markers", "real_scanner_run: usa scanners._run real")


@pytest.fixture(autouse=True)
def _no_external_scanners(request, monkeypatch):
    if request.node.get_closest_marker("real_scanner_run"):
        return
    monkeypatch.setattr(
        scanners,
        "_run",
        lambda cmd, cwd, timeout=300: {
            "tool": cmd[0], "status": "TOOL_MISSING", "returncode": None,
            "stdout": "", "stderr": "",
        },
    )
