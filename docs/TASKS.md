# Starter Tasks

- [x] T01 Run baseline tests. (31 passed + 1 xfailed intencional; `python scripts/verify_repo.py`)
- [x] T02 Configure Google AI Studio API key. (GOOGLE_API_KEY en `.env` — verificada con llamada ping; .env gitignoreado y ausente de todo el historial git; scanner cubre formato `AQ.`)
- [ ] T03 Run Intake/Planner in ADK. (código listo: `python -m factory_app.orchestrator CR-001 --adk`; falta corrida real con key)
- [ ] T04 Execute CR-001 through planning. (demo fake end-to-end ✅; falta con Gemini real)
- [x] T05 Implement human plan gate pause/resume. (`orchestrator.advance` + `approve_gate.py`; silence is never approval — testeado)
- [x] T06 Add bounded write capability for Coder. (`tools.write_product_file`: campusflow/+tests/ only; rechazos testeados en `test_tools_bounded.py`) + git evidence: rama `factory/<CR>`, commit de maquina y diff-stat registrados en el coder-report (testeado con repo real en tmp).
- [x] T07 Wire deterministic verification. (pytest+ruff gate; FAIL → backedge acotada; nunca llega a review)
- [ ] T08 Run Reviewer + Security independently. (estructura y sesiones separadas listas; falta corrida real --adk)
- [x] T09 Implement human finding decisions. (`decide_findings.py` ACCEPTED/REJECTED/MODIFIED/DEFERRED; sin decisión no se aplica nada — testeado)
- [x] T10 Implement fix -> reverify -> rereview. (rounds con decisiones archivadas por ronda; MAX_FIX_ROUNDS=3 — testeado)
- [x] T11 Generate traceability + `factory-run.json`. (shape exacto del curso, generado por el motor — testeado + demo)
- [ ] T12 Create 5+ agent eval cases. (dataset 7 casos + métrica adversarial listos; falta `agents-cli eval run` real con key)

Restante principal = correr de verdad con Gemini (T02→T03/T04/T08), 3 CRs completos, evals y defensa.

## Runbook de corridas reales (requiere GEMINI_API_KEY y cuota; no ejecutado)

Pendiente del humano; la fábrica ya soporta checkpoint/resume (`docs/RATE_LIMIT_PLAYBOOK.md`).
Un run completo gasta ~15-40 llamadas: repartir en ventanas de cuota y rotar roles entre CRs.

1. Feature normal: `CR-001` → `start_run.py`, `orchestrator CR-001 --adk`, gates con rol correcto,
   `export_evidence.py CR-001`.
2. Bug/regresión: `CR-003` (el xfail strict debe pasar a PASS sin debilitar tests).
3. Autorización/ambigüedad: `CR-002` o `CR-005` (debe STOPear; el PO decide y el run reanuda).
4. Evals: `agents-cli eval run` → guardar `baseline.json`; mejorar; `eval run` →
   `improved.json`; `agents-cli eval compare baseline.json improved.json`.
5. Defensa: CR sorpresa con el mismo flujo; registrar roles que firmaron cada gate.
