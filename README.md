# CampusFlow + Agentic Software Factory

Repositorio del curso UADY **Ingeniería de Software asistida por IA**, sobre el starter oficial.

Dos sistemas separados:

1. **`campusflow/`** — el producto que la fábrica evoluciona.
2. **`factory_app/`** — la fábrica (Google ADK + orquestador propio con human gates).

## Reference stack

Python 3.11+ · Google ADK · Gemini Developer API (AI Studio) · Agents CLI · Git/GitHub · pytest + ruff · Semgrep/pip-audit/detect-secrets · GitHub Actions. Sin Google Cloud billing.

## Setup

```bash
uv sync
cp .env.example .env        # pegar GEMINI_API_KEY de Google AI Studio (GOOGLE_API_KEY tambien vale)
uv run pytest -q            # 82 passed + 1 xfailed (defecto intencional CR-003)
uv run ruff check campusflow tests factory_app scripts
python scripts/verify_repo.py
```

## Cómo opera la fábrica (implementado)

```bash
# 1. abrir un run
uv run python scripts/start_run.py CR-001

# 2. avanzar fases (se DETIENE solo en cada human gate; sin veredicto no sigue)
uv run python -m factory_app.orchestrator CR-001          # plantillas fake (sin API)
uv run python -m factory_app.orchestrator CR-001 --adk    # agentes Gemini reales (requiere .env)

# 3. decisiones humanas (cada una firma nombre+rol y queda como artefacto)
uv run python scripts/approve_gate.py CR-001 requirements APPROVED "interpretacion OK" --by "Hector (Product Owner)"
uv run python scripts/approve_gate.py CR-001 plan APPROVED "alcance acotado" --by "Hector (Tech Lead)"
uv run python scripts/decide_findings.py CR-001 --by "Ana (Human Reviewer)" RF-01=ACCEPTED RF-02=DEFERRED
uv run python scripts/approve_gate.py CR-001 review APPROVED "hallazgos clasificados" --by "Ana (Human Reviewer)"
uv run python scripts/approve_gate.py CR-001 release APPROVED "evidencia completa" --by "Hector (Release Manager)"

# 4. inspección
uv run python scripts/show_run.py CR-001
cat runs/CR-001/factory-run.json runs/CR-001/traceability.md
```

Flujo de fases: `intake → gate requirements → plan → gate plan → coder+test analyst →
verification(pytest/ruff determinista) → reviewer ∥ security (sesiones separadas, solo lectura) →
gate findings → (fix → re-verification → re-review, ≤3 rondas) → evidencia consolidada →
gate release → READY FOR HUMAN MERGE`.

Garantías estructurales (no de prompt):

- Gates: `advance()` persiste estado y **termina el proceso** sin veredicto registrado.
- Verification FAIL → nunca corre Reviewer/Security.
- El Coder solo puede escribir `campusflow/` y `tests/` (`write_product_file`); docs/fábrica/evidencia/config están denegados en runtime.
- Findings sin decisión humana ACCEPTED/MODIFIED no cambian código; tras un fix, la nueva revisión exige decisiones de la ronda actual.
- `factory-run.json`, `traceability.md` y `14-evidence-summary.md` los genera el motor con contadores parseados de evidencia real.
- Estado máximo = READY FOR HUMAN MERGE; no existe tool de merge.

Alineación con el instructivo (§8-§18):

- Contratos por agente en `factory_app/contracts.py` (role, context, allow, deny, output,
  stop conditions, escalation), validados contra las tools reales de cada agente.
- Test Analyst puede escribir solo `tests/product/test_*.py` (`write_test_file`); Reviewer y
  Security leen el diff con `inspect_git_diff` (solo lectura).
- Context engineering: `select_context` elige secciones de REQUIREMENTS/SPEC, ARCHITECTURE,
  fuentes y tests relevantes desde `docs/REPO_MAP.md` y deja `runs/<CR>/context-manifest.json`
  (incluido y descartado).
- Cada gate exige el rol correcto en `--by "Nombre (Rol)"` (`scripts/_roles.py`).
- Un `ESCALATED` por ambigüedad se reanuda cuando el PO registra una decisión posterior; los
  rechazos de gate no se reanudan.
- Re-review: la ronda anterior se archiva (`08-review-report-roundN.md`), se escribe
  `13-re-review.md`, y los reportes de una ronda no se regeneran al reanudar tras el gate.
- Solo un MUST FIX / HIGH aceptado dispara fix; un SHOULD FIX aceptado queda registrado.
- `traceability.md` solo enlaza lo evidenciable; lo demás es `UNMAPPED` y se cuenta en
  `traceability_gaps` de `factory-run.json`.

Capa LLM desacoplada (`factory_app/orchestrator.py`): `FakeExecutor` (CI/sin cuota,
comportamiento determinista) ↔ `ADKExecutor` (Gemini real). Cambiar de proveedor no toca
gates, permisos, evidencia ni estado.

## Evals

Dataset ampliado en `tests/eval/datasets/basic-dataset.json` (7 casos: normal, ambiguo,
regresión, API alucinada, defecto de seguridad, abuso de prompt, scope control) con
métricas `factory_planning_quality` + `adversarial_compliance`:

```bash
agents-cli eval run        # requiere GEMINI_API_KEY
```

## Change Requests

`CR-001` waitlist · `CR-002` autorización owner/admin · `CR-003` bug duplicados (xfail intencional) · `CR-004` CSV export · `CR-005` ambiguo (la fábrica debe STOPear).

## Estado

Ver `PROJECT_MILESTONES.md` y `docs/TASKS.md`. Pendiente principal: corridas reales con
Gemini (T02–T04/T08), 3 CRs completos con evidencia, eval compare y defensa con CR sorpresa.
