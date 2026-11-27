# Factory Run Evidence

Estructura real de cada run (curso §17), generada por `factory_app/orchestrator.py`:

```text
runs/CR-001/
  00-change-request.md
  01-intake-report.md
  02-human-requirement-decision.md      # gate 1 (Product Owner)
  03-implementation-plan.md
  04-human-plan-approval.md             # gate 1 (Tech Lead)
  05-coder-report.md
  06-test-analysis.md
  07-verification.txt
  08-review-report.md
  09-security-report.md
  10-human-review-decisions.md          # gate 2 (Human Reviewer), append-only por ronda
  11-fix-report.md                      # solo si hubo findings aceptados bloqueantes
  12-re-verification.txt                # solo si hubo fix
  13-re-review.md                       # Reviewer + Security tras el fix
  14-evidence-summary.md
  traceability.md
  factory-run.json
  15-release-decision.json              # gate 3 (Release Manager)
  context-manifest.json                 # contexto seleccionado: incluido / descartado
  tool-calls.jsonl                      # solo --adk: cada llamada a tool (fase, agente, args truncados, ok/error)
  factory-state.json                    # estado/checkpoint del motor (awaiting_gate, base_commit, rondas)
  human-<gate>-decision.json            # fuente maquina de cada decision humana
  human-<gate>-decision-clarification*.json  # aclaraciones del PO que reanudaron un ESCALATED
  human-findings-decisions.json         # decisiones por finding (ACCEPTED/REJECTED/MODIFIED/DEFERRED)
  08-review-report-roundN.md, 09-security-report-roundN.md   # rondas previas archivadas
```

Reglas de los artefactos:

- `human-*` solo los escriben `approve_gate.py` / `decide_findings.py`, y solo valen si se
  registraron con el run detenido en ese gate (despues de `gate_opened_at`).
- `factory-run.json`, `traceability.md` y `14-evidence-summary.md` tambien se generan cuando el
  run queda `ESCALATED`, para que `export_evidence.py` pueda exportarlo.
- `07-verification.txt` / `12-re-verification.txt` incluyen el resultado `test-integrity`
  (tests eliminados o skip/xfail nuevos respecto a la base del run).
- `09-security-report.md` lista los HIGH de escaner como `SEC-SCAN-NN | HIGH | ...` y los
  escaneres degradados (`TOOL_MISSING` / `PARSE_ERROR` / `FALLBACK_REGEX`).
- `05-coder-report.md` y `06-test-analysis.md` anexan su "Git evidence" (commit y diff
  `<base_commit>...factory/<CR>`).
