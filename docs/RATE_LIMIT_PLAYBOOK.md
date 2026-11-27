# Rate-Limit / Quota Playbook (evidencia real, no hipotética)

Esto ocurrió de verdad durante T03 y así lo maneja la fábrica. Es la evidencia viva
de "Resilience: manejo documentado de rate limit / retry / resume" (Definition of Done).

## Cronología observada (2026-11-19, key free tier)

1. Ping de verificación con `gemini-3.8-flash` → OK.
2. Intake real de CR-001 → **503 UNAVAILABLE** ("model experiencing high demand") tras
   ~5 min de reintentos internos de ADK (tenacity).
3. Reintento → **429 RESOURCE_EXHAUSTED**: *quota diaria free tier* de
   `generate_content_free_tier_requests`, **limit: 20/día por modelo**, `retryDelay: 20000s`.
4. Routing a `gemini-3.5-flash-lite` (pool de quota independiente) → operativo de nuevo.

## Comportamiento implementado en `factory_app/orchestrator.py`

| Situación | Respuesta de la fábrica |
|---|---|
| 429/503 transitorios (`429`, `503`, `RESOURCE_EXHAUSTED`, `UNAVAILABLE`, "high demand", "rate limit"; sin distinguir mayúsculas) | Exponential backoff (base 3s, x2) + jitter, hasta 5 reintentos |
| `RetryInfo.retryDelay > 10 min` (cuota diaria) | **No reintenta** (gastaría quota en vano); eleva `TransientModelError` con la ventana de resume y la sugerencia de cambiar modelo |
| Agotados reintentos | Estado del run **intacto en su checkpoint** (`INTAKE_PENDING` verificado: cero escrituras parciales); exit 4 |
| Reanudación | El MISMO comando `--adk` continúa desde el último checkpoint; fases ya completas no se repiten |
| Reintentos internos de google-genai | **Desactivados** (`Gemini(retry_options=HttpRetryOptions(attempts=1))` en `factory_app/agents.py`): solo reintenta el backoff de la fábrica, así no se multiplican los reintentos ni se esconde la métrica |
| Métrica | `llm_retries` y `last_llm_note` persistidos en `factory-state.json` (medible en evals §19.1); cuenta tanto los reintentos que terminaron en éxito como los agotados |
| Respuesta vacía o sin línea `RECOMMENDATION:` (Intake/Planner) | **No** se toma como PROCEED: el run queda `ESCALATED` (`stop_reason` "sin RECOMMENDATION valida") y se reanuda con una decisión del gate correspondiente |
| Error no transitorio (p. ej. `KeyError` de plantilla ADK) | Sin reintento: se propaga y el run sigue en su checkpoint |
| Cambio de proveedor/modelo | `FACTORY_MODEL` en `.env` sin tocar código (routing 3.8-flash ↔ 3.5-flash-lite observado en producción) |

## Reglas del curso que cumplimos (y una que NO hacemos)

- ✅ Herramientas deterministas cuando el LLM no aporta (gates, verificación).
- ✅ Checkpoint + resume.
- ✅ Backoff ante `RESOURCE_EXHAUSTED`.
- ❌ Prohibido y no practicado: múltiples API keys del mismo proyecto para evadir cuota.
  La alternativa legítima es el **model routing** (cada modelo tiene su propia ventana de
  quota diaria) y distribuir los runs entre ventanas de 24h.

## Presupuesto realista de free tier para los runs

- Fase de agente ≈ 1-4 llamadas (tool round-trips); un run completo (intake→plan→
  implement→reviews, con re-rondas) ≈ 15-40 llamadas.
- Con 2 pools (flash 20/día + flash-lite 20/día): un CR completo por día como máximo
  holgado → los 3 CRs se programan en 2-3 días o en ventanas post-reset.
- La defensa en vivo usa 1 sola fase visible por ventana; el pipeline fake/CI no consume
  quota, así la demostración de arquitectura nunca depende de la red.

## Cómo reproducir la prueba (eval E-rate-limit)

```bash
FACTORY_MODEL=gemini-3.8-flash python -m factory_app.orchestrator CR-XXX --adk
# -> TransientModelError con ventana de resume; factory-state.json conserva INTAKE_PENDING
FACTORY_MODEL=gemini-3.5-flash-lite python -m factory_app.orchestrator CR-XXX --adk
# -> reanuda y avisa donde debe (gate)
```
