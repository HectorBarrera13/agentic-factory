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
uv run pytest -q            # 175 passed + 1 xfailed (defecto intencional CR-003)
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

# 3. decisiones humanas: SOLO cuando el run se detuvo en ese gate (no hay pre-aprobaciones).
#    Cada una firma nombre+rol y queda como artefacto; tras cada una, repetir el paso 2.
uv run python scripts/approve_gate.py CR-001 requirements APPROVED "interpretacion OK" --by "Hector (Product Owner)"
uv run python scripts/approve_gate.py CR-001 plan APPROVED "alcance acotado" --by "Hector (Tech Lead)"
#    gate review: clasificar TODOS los findings (las llamadas se acumulan) y cerrar el gate
uv run python scripts/decide_findings.py CR-001 --by "Ana (Human Reviewer)" RF-01=ACCEPTED RF-02=DEFERRED
uv run python scripts/decide_findings.py CR-001 --by "Ana (Human Reviewer)" SEC-SCAN-01=REJECTED
uv run python scripts/approve_gate.py CR-001 review APPROVED "hallazgos clasificados" --by "Ana (Human Reviewer)"
uv run python scripts/approve_gate.py CR-001 release APPROVED "evidencia completa" --by "Hector (Release Manager)"

# 4. inspección
uv run python scripts/show_run.py CR-001
cat runs/CR-001/factory-run.json runs/CR-001/traceability.md
```

Flujo de fases (estado en `factory-state.json`): `INTAKE_PENDING → REQUIREMENTS_GATE →
PLAN_PENDING (plan + gate plan) → IMPLEMENT_PENDING (coder+test analyst en factory/<CR>) →
VERIFY_PENDING (pytest/ruff + integridad de tests) → REVIEW_PENDING (reviewer ∥ security,
sesiones separadas, solo lectura + gate review) → (fix → re-verification → re-review) →
EVIDENCE_PENDING (evidencia + gate release) → READY FOR HUMAN MERGE | NOT READY | ESCALATED`.

Garantías estructurales (no de prompt):

- Gates: `advance()` persiste estado y **termina el proceso** sin veredicto registrado.
- Una decisión solo vale si se registra **después** de que el run se detuvo en ese gate
  (`awaiting_gate` + `gate_opened_at`); `approve_gate.py` rechaza cualquier otro gate. Reanudar
  sin decisión no salta el gate ni regenera el intake/plan/reviews ya presentados.
- Gates `requirements`, `plan` y `review` con REJECTED/DEFERRED → `ESCALATED` (no se reanuda);
  `release` REJECTED → `NOT READY`.
- Verification FAIL → nunca corre Reviewer/Security. La verificación también falla si en
  `tests/product` desaparece un `test_*` o aparece un skip/xfail nuevo respecto a la base del run
  (quitar un xfail sí se permite).
- Topes separados: 3 rondas de fix por verificación y 3 por findings; al superarlos → `ESCALATED`.
- Git: `factory/<CR>` se crea desde el HEAD al aprobar el plan y guarda `base_commit`; todos los
  diffs son `<base_commit>...factory/<CR>`. Antes del Coder se hace checkout y, si hay cambios sin
  commitear en `campusflow/` o `tests/product`, el run se bloquea (no se mezclan con el commit de
  máquina).
- El Coder solo puede escribir `campusflow/` y `tests/product/` (`write_product_file`); docs/fábrica/evidencia/config están denegados en runtime.
- El Evidence Agent no puede escribir `human-*`, estado, consolidados ni evidencia numerada `NN-*`.
- Findings sin decisión humana ACCEPTED/MODIFIED no cambian código; tras un fix, la nueva
  revisión exige decisiones de la ronda actual. `approve_gate review APPROVED` exige todos los
  findings del reporte clasificados. Los HIGH de los escáneres reciben id `SEC-SCAN-NN` y se
  clasifican igual; un escáner ausente o ilegible queda `TOOL_MISSING`/`PARSE_ERROR` (degradado,
  nunca "OK").
- Intake y Planner deben terminar con `RECOMMENDATION: PROCEED|HUMAN_CLARIFICATION_REQUIRED`;
  respuesta vacía o sin esa línea → `ESCALATED`.
- `factory-run.json`, `traceability.md` y `14-evidence-summary.md` los genera el motor con
  contadores parseados de evidencia real (también al escalar, para poder exportarlo).
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
- Un `ESCALATED` por ambigüedad se reanuda cuando el PO registra una decisión posterior; esa
  aclaración solo reanuda (se archiva como `human-<gate>-decision-clarification.json`) y el
  intake/plan regenerado vuelve a pasar por su gate con una decisión nueva. Los rechazos de
  gate no se reanudan.
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
