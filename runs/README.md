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
  factory-state.json                    # estado/checkpoint del motor
  human-<gate>-decision.json            # fuente maquina de cada decision humana
  human-findings-decisions.json         # decisiones por finding (ACCEPTED/REJECTED/MODIFIED/DEFERRED)
  08-review-report-roundN.md, 09-security-report-roundN.md   # rondas previas archivadas
```
