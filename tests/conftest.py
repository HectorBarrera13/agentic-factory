"""Los tests no dependen de que semgrep/pip-audit/detect-secrets esten instalados ni de la
red: por defecto los escaneres externos se ven como TOOL_MISSING (semgrep --config auto
descarga reglas en cada review y multiplicaba x6 la duracion de la suite, rompiendo el
timeout de la verificacion de la propia fabrica).

Los tests de escaneres que necesitan el `_run` real se marcan con `real_scanner_run`.
Aplica a toda la suite (fabrica y Factory Studio).
"""

from __future__ import annotations

import os

import pytest

from factory_app import scanners


def pytest_configure(config):
    config.addinivalue_line("markers", "real_scanner_run: usa scanners._run real")
    config.addinivalue_line("markers", "real_probe: usa probe_in_background real")


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


@pytest.fixture(autouse=True)
def _isolated_user_home(request, tmp_path, monkeypatch):
    """Credenciales/probes nunca tocan ~/.agentic-factory ni la red real en los tests."""
    from factory_app import credentials, probe, providers

    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "factory-home"))
    monkeypatch.setattr(credentials, "_applied", {})
    # apply_to_env escribe os.environ: registrar cada variable para restaurarla al terminar
    for keys, url_var in credentials.ENV_VARS.values():
        for var in (*keys, *([url_var] if url_var else [])):
            if var in os.environ:
                monkeypatch.setenv(var, os.environ[var])
            else:
                monkeypatch.delenv(var, raising=False)
    providers.clear_cache()
    if not request.node.get_closest_marker("real_probe"):
        monkeypatch.setattr(probe, "probe_in_background", lambda pairs, max_age=3600: [])
