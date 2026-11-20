# Project Milestones

## M0 — Baseline ✅
Run tests; audit repo; configure API key; assign human roles.
Tests verdes (31 passed + 1 xfailed intencional). Repo git inicializado. **Pendiente del humano:** pegar `GOOGLE_API_KEY` en `.env` y roles definitivos del equipo.

## M1 — Planning factory ✅ (estructura verificada en fake)
Intake + Planner + human plan gate. CR-005 must stop for clarification.
Implementado en `factory_app/orchestrator.py` con gates bloqueantes reales; verificado con `FakeExecutor` (tests) y demo CR-001 end-to-end. Falta corrida real con Gemini.

## M2 — Implementation + verification ✅
Bounded Coder tools (`write_product_file`: campusflow/ + tests/ ONLY), isolated change (rama `factory/<CR>`), Test Analyst, deterministic verification (pytest+ruff via `run_baseline_verification`). FAIL nunca llega a review; fix rounds acotadas (MAX=3).

## M3 — Review + Security ✅ (estructura)
Independent read-only Reviewer y Security (sesiones separadas, contexto desde artefactos+diff), evidence-based findings, human decisions ACCEPTED/REJECTED/MODIFIED/DEFERRED via `scripts/decide_findings.py`. Semgrep/pip-audit/secrets integrados en `factory_app/scanners.py` (degradación honesta si la herramienta falta: se reporta TOOL_MISSING, nunca se simula evidencia).

## M4 — Fix / re-review / release ✅ (estructura)
Accepted blocking MUST FIX -> Coder Fix -> re-verification -> re-review con decisiones NUEVAS por ronda (decisiones consumidas se archivan `-roundN.json`). Release gate -> READY FOR HUMAN MERGE / NOT READY. `factory-run.json` + `traceability.md` + `14-evidence-summary.md` generados por el motor, no por el LLM.

## M5 — Agent evaluation 🟡
Dataset ampliado a 7 casos (E01–E06 + scope_control) con prompts adversariales; métrica `adversarial_compliance` añadida. **Pendiente:** `agents-cli eval run` real (requiere API key) + baseline vs improved.

## Final defense 🟡
Simulacro con CR sorpresa todavía no ejecutado; la estructura lo soporta (`start_run.py` + `orchestrator --adk`).
