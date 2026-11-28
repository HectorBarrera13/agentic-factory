# Repository Map

| Path | Purpose |
|---|---|
| `campusflow/domain.py` | Product entities |
| `campusflow/repository.py` | SQLite persistence |
| `campusflow/service.py` | Business rules |
| `campusflow/api.py` | HTTP adapter |
| `factory_app/agent.py` | ADK root agent |
| `factory_app/agents.py` | Specialized agents |
| `factory_app/tools.py` | Bounded tools |
| `factory_app/prompts.py` | Agent prompts |
| `factory_app/contracts.py` | Structured agent contracts (role/allow/deny/output/stop/escalation) |
| `factory_app/orchestrator.py` | Gates, state machine, evidence, traceability |
| `scripts/_roles.py` | Human role required per gate |
| `scripts/retry_run.py` | New attempt of an ESCALATED / NOT READY run (edited CR, PO signature) |
| `change_requests/` | Work input |
| `scripts/` | Deterministic run/gate helpers |
| `tests/` | Product, factory and eval tests |
| `runs/` | Generated evidence |

Notes:

- `factory_app/workflows.py` (`ParallelAgent`) serves the ADK/`agents-cli` playground. The real
  orchestrator runs Reviewer and Security sequentially, each in its own fresh session, to
  conserve Free Tier quota (course §20.1).
- `factory_app.tools.select_context` discovers product files from the table above (rows with a
  backticked `.py` path) and writes `runs/<CR>/context-manifest.json`; keep that format.
