# CampusFlow + Agentic Software Factory

Repositorio del curso UADY **Ingeniería de Software asistida por IA**, sobre el starter oficial.

Dos sistemas separados:

1. **`campusflow/`** — el producto que la fábrica evoluciona.
2. **`factory_app/`** — la fábrica (Google ADK + orquestador propio con human gates).

## Reference stack

Python 3.11+ · Google ADK · Gemini Developer API (AI Studio) por defecto; Anthropic, OpenAI, Ollama y servidores compatibles con OpenAI opcionales · Agents CLI · Git/GitHub · pytest + ruff · Semgrep/pip-audit/detect-secrets · GitHub Actions. Sin Google Cloud billing.

## Setup

```bash
uv sync
cp .env.example .env        # pegar GEMINI_API_KEY de Google AI Studio (GOOGLE_API_KEY tambien vale)
uv run pytest -q            # 283 passed + 1 xfailed (defecto intencional CR-003)
uv run ruff check campusflow tests factory_app factory_ui scripts
python scripts/verify_repo.py
```

## Factory Studio (UI local)

IDE web local: a un lado tu código (explorador + editor Monaco), al otro tu fábrica
(Change Requests, runs con diagrama de fases en vivo, gates firmados, findings, diff y ajustes del flujo).
Cada run muestra un **diagrama del flujo en vivo**: quién trabaja (rol, proveedor y modelo), en qué
tool o comando, cuánto lleva, a quién espera cada gate y las rondas de fix; debajo, la tira **Ahora**
y la pestaña **Actividad** (registro por fase). Sale de `runs/<CR>/activity.jsonl`, que el motor escribe
con datos reales (nunca el texto del modelo ni el contenido de los archivos). El diagrama se contrae a
una tira de fases y respeta `prefers-reduced-motion`.
Funciona sobre **cualquier proyecto git**: su configuración vive en `<proyecto>/.factory/config.toml`
(este repo ya trae la suya, que apunta a `change_requests/` y `runs/`).

```bash
cd ui && npm install && npm run build && cd ..   # una vez (Node solo para compilar el frontend)
uv run python -m factory_ui [RUTA_PROYECTO]       # abre http://127.0.0.1:8765/?token=...
```

- **Abrir proyecto**: sin ruta, Studio abre el último proyecto abierto que siga existiendo (si no
  hay, la carpeta actual); una ruta explícita gana. "Examinar…" abre el explorador de carpetas del
  sistema (tkinter, en un proceso aparte; si falta, se escribe la ruta a mano). Si el proyecto no
  tiene `.factory/`, el asistente detecta lenguaje, carpetas y comandos (pyproject / package.json),
  propone como modelo el último que pasó la prueba y tú revisas antes de guardar.
- **Rutas**: la vista queda en la URL (`#/factory/CR-04`, `#/settings/providers`,
  `#/code?root=…&path=…`); recargar o "atrás" vuelve al mismo lugar.
- **Modo del run**: por defecto *Modelo real*; *Simulación (Fake)* es para probar el flujo sin
  modelo. Antes de avanzar con un modelo que no pasó la prueba, el tablero avisa y pide
  confirmación; también avisa si `FACTORY_MODEL` del entorno reemplaza el modelo de Ajustes.
- **Pausar / reanudar**: "Pausar" corta ya (cancela la llamada al modelo en curso o la espera de
  reintento) y el run queda *pausado* en su último punto de control. "Reanudar" repite esa fase
  desde su inicio. Si se cortó al Coder, sus cambios a medias en el worktree del run se descartan
  (nunca en tu árbol). La pausa y la reanudación quedan en `transitions` (`PAUSED`/`RESUMED`). La
  verificación (tests/lint) no se corta: termina y la pausa se aplica justo después.
- **Escalados**: el tablero distingue el escalado *reanudable* (el agente pide una aclaración: la
  registras como decisión en el gate y pulsas Avanzar) del *definitivo* (gate rechazado o diferido,
  topes de rondas, aclaraciones agotadas). Un run `ESCALATED` o `NOT READY` muestra **Reintentar**:
  editas el CR, escribes el motivo y firmas como Product Owner, y empieza un intento nuevo del mismo
  CR (ver "Reintentar" abajo).
- **Worktree por run**: al aprobar el plan, el run trabaja en `.factory/worktrees/<CR>` con su rama
  `factory/<CR>`; tu árbol no se toca y puedes seguir editando. El worktree solo se borra con
  "Quitar worktree" (la rama se conserva). Nunca hay merge automático.
- **Perfiles locales** (`~/.agentic-factory/profiles.json`): nombre + roles del catálogo
  (Product Owner, Tech Lead, Human Reviewer, Reviewer, Release Manager; no se aceptan otros). Cada
  decisión se firma como `Nombre (Rol)` y el rol se valida contra los roles del gate del proyecto.
  Un perfil viejo con roles fuera del catálogo carga marcado como incompleto hasta editarlo.
- **Ajustes**: proveedor, modelo y temperatura por agente (ver abajo), límites de rondas, comandos de verificación
  (`pytest` / `jest` / `ruff` / `lint` / `exit_code`; un `exit_code` que corre `node --test` también
  cuenta sus tests), rutas, roles por gate. El orden de fases y
  gates es fijo. La guardia de integridad de tests solo existe para Python ("no disponible" en
  otros lenguajes).
- **Seguridad**: solo escucha en 127.0.0.1, valida `Host`, token por sesión (cookie HttpOnly),
  sin CORS; `.git` no es accesible y los `.env*`/claves se listan sin contenido.
- Desarrollo del frontend: `uv run python -m factory_ui --token dev --no-browser` y `npm run dev`
  en `ui/` (Vite en :5173 hace proxy de `/api` con el token). Tests: `npm test`, `npm run typecheck`
  (TypeScript 7).

Los scripts CLI siguen funcionando y aceptan `--project RUTA` (por defecto, este repo).

### Proveedores de modelos

| Proveedor | Acceso | Adaptador ADK |
|---|---|---|
| Google Gemini (por defecto) | API key (AI Studio) | `Gemini` |
| Anthropic | API key | `AnthropicLlm` |
| OpenAI | API key | `LiteLlm` (`openai/…`) |
| Ollama (local) | URL, por defecto `http://localhost:11434` | `LiteLlm` (`ollama_chat/…`) |
| Compatible con OpenAI (LM Studio, vLLM, llama.cpp…) | URL `/v1` + clave opcional | `LiteLlm` (`openai/…`) |

- **Claves**: en *Ajustes → Proveedores* eliges el proveedor, pegas la clave (o la URL) y
  "Guardar y probar" hace una llamada de listado: *Conectado · N modelos*, *clave inválida* o
  *sin conexión*. Se guardan solo en `~/.agentic-factory/credentials.json` (fuera del proyecto);
  la UI muestra únicamente los 4 últimos caracteres. Una variable de entorno real
  (`GEMINI_API_KEY`/`GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_HOST`,
  `OPENAI_COMPATIBLE_BASE_URL`, o el `.env`) tiene prioridad y no se edita desde la UI.
- **Proveedor por proyecto y por agente**: `[agents] provider/model` es el valor por defecto y
  `[agents.roles.<agente>]` puede cambiar proveedor, modelo o temperatura (si cambia de proveedor,
  debe indicar modelo). `FACTORY_MODEL` solo aplica cuando el proveedor es Gemini.
- **Lista de modelos dinámica**: sale del endpoint de listado de cada proveedor (cache de 10 min,
  botón para recargar); también se puede escribir un nombre a mano.
- **Probe de modelos**: "Probar" (y, al guardar Ajustes, automáticamente para los modelos en uso)
  hace una llamada mínima real con una tool `ping`: *disponible*, *no usa tools* (no sirve para los
  agentes), *sin acceso en tu plan*, *sin cuota*, *clave inválida* o *error*. Los resultados se
  guardan en `~/.agentic-factory/model-probes.json`; los fallos se vuelven a probar al guardar.
- **Temperatura**: cada proveedor tiene su rango nativo (Gemini/OpenAI/locales 0–2, Anthropic 0–1).
  El valor se recorta a ese rango y, si el probe detectó que el modelo no acepta temperatura
  (algunos modelos de razonamiento), no se envía. La UI avisa "se aplicará X" o "no acepta
  temperatura"; lo pedido y lo efectivo por agente quedan en `factory-state.json` y
  `factory-run.json` (`models`).
- Reintentos: solo el backoff del orquestador reintenta (los SDKs van con 0 reintentos); los
  errores 429/529/overloaded/servicio no disponible de cualquier proveedor son transitorios y los de
  autenticación o modelo inexistente escalan al humano.

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

#    run ESCALATED o NOT READY: edita el archivo del CR y abre un intento nuevo (firma el PO)
uv run python scripts/retry_run.py CR-001 "alcance aclarado" --by "Hector (Product Owner)"

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
  diffs son `<base_commit>...factory/<CR>`. El run trabaja en su worktree
  `.factory/worktrees/<CR>` (tu árbol no cambia de rama); si el worktree tiene cambios sin
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
- Las aclaraciones del PO se **acumulan** (`po_clarifications`): el agente recibe todas las
  respuestas anteriores numeradas y no debe volver a preguntarlas. Tienen tope por intento
  (`limits.clarification_rounds`, 3 por defecto): si el agente vuelve a pedir aclaración con el
  tope agotado, el run queda `ESCALATED` sin gate abierto ("aclaraciones agotadas") y solo sigue
  con Reintentar.
- **Reintentar** (`retry_run`, Studio o `scripts/retry_run.py`), solo desde `ESCALATED` o
  `NOT READY` y con firma del Product Owner (roles del gate `requirements`). No reanuda nada: el
  intento anterior se archiva entero en `runs/<CR>/intentos/<n>/`, su rama se renombra a
  `factory/<CR>-intento<n>` (se conserva, no se borra ni se mergea; se quita solo su worktree) y el
  intento nuevo arranca en `INTAKE_PENDING` con el CR editado y todos los gates en `PENDING`.
  Queda `00-retry-decision.md` (quién, por qué, intento anterior y si cambió el CR) y
  `factory-run.json` incluye `attempt` y `previous_attempts`. Pasado `limits.attempts` (3 por
  defecto) se exige confirmación explícita (`--force`), que también queda registrada.
- Re-review: la ronda anterior se archiva (`08-review-report-roundN.md`), se escribe
  `13-re-review.md`, y los reportes de una ronda no se regeneran al reanudar tras el gate.
- Solo un MUST FIX / HIGH aceptado, o un finding que el Human Reviewer marca "corregir en este
  ciclo", dispara fix; un SHOULD FIX aceptado sin esa marca queda registrado. Cada
  clasificación lleva su motivo (llega al Coder Fix) y el Human Reviewer puede añadir findings
  propios (`HR-NN`, en `08b-human-review-findings.md`), que quedan aceptados por quien los añade.
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

Ciclo de vida de un CR en Studio (lo decide el servidor, `services.cr_lifecycle`):

- **Editar**: mientras no tenga run, o con el run creado pero sin el primer Avanzar (intake sin
  correr, sin pausa, sin ejecución en curso). El cambio llega también a `runs/<CR>/00-change-request.md`.
  Después del intake el CR queda congelado como evidencia; para cambiarlo, **Reintentar**.
- **Archivar** (solo runs terminados: `READY FOR HUMAN MERGE`, `NOT READY` o `ESCALATED` sin gate
  abierto): mueve el CR a `<crs>/archivados/` y el run a `<runs>/archivados/<CR>/` con
  `archivo.json` (quién, cuándo, estado final), quita el worktree y conserva la rama. Se restaura
  desde la pestaña *Archivados* (o con *Deshacer* en el aviso).
- **Eliminar**: permanente; pide teclear el ID y una firma. Borra el CR, la carpeta del run y el
  worktree; la rama se conserva. Vale para runs terminados, archivados y CRs sin run.

## Estado

Ver `PROJECT_MILESTONES.md` y `docs/TASKS.md`. Pendiente principal: corridas reales con
Gemini (T02–T04/T08), 3 CRs completos con evidencia, eval compare y defensa con CR sorpresa.
