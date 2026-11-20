# Starter Tasks

- [x] T01 Run baseline tests. (31 passed + 1 xfailed intencional; `python scripts/verify_repo.py`)
- [ ] T02 Configure Google AI Studio API key. (pegar `GOOGLE_API_KEY=...` en `.env` — requiere humano)
- [ ] T03 Run Intake/Planner in ADK. (código listo: `python -m factory_app.orchestrator CR-001 --adk`; falta corrida real con key)
- [ ] T04 Execute CR-001 through planning. (demo fake end-to-end ✅; falta con Gemini real)
- [x] T05 Implement human plan gate pause/resume. (`orchestrator.advance` + `approve_gate.py`; silence is never approval — testeado)
- [x] T06 Add bounded write capability for Coder. (`tools.write_product_file`: campusflow/+tests/ only; rechazos testeados en `test_tools_bounded.py`)
- [x] T07 Wire deterministic verification. (pytest+ruff gate; FAIL → backedge acotada; nunca llega a review)
- [ ] T08 Run Reviewer + Security independently. (estructura y sesiones separadas listas; falta corrida real --adk)
- [x] T09 Implement human finding decisions. (`decide_findings.py` ACCEPTED/REJECTED/MODIFIED/DEFERRED; sin decisión no se aplica nada — testeado)
- [x] T10 Implement fix -> reverify -> rereview. (rounds con decisiones archivadas por ronda; MAX_FIX_ROUNDS=3 — testeado)
- [x] T11 Generate traceability + `factory-run.json`. (shape exacto del curso, generado por el motor — testeado + demo)
- [ ] T12 Create 5+ agent eval cases. (dataset 7 casos + métrica adversarial listos; falta `agents-cli eval run` real con key)

Restante principal = correr de verdad con Gemini (T02→T03/T04/T08), 3 CRs completos, evals y defensa.
