"""Los 7 agentes ADK de la fabrica.

Cada rol se construye con una funcion `make_*_agent()`: el orquestador crea una instancia
NUEVA por fase (sin padre ni estado compartido) y `workflows.py` crea las suyas para el
playground. Las instancias de modulo (`intake_agent`, ...) existen solo para introspeccion
(tests de contratos/permisos) y nunca se ejecutan.

Configuracion comun:
- `disallow_transfer_*` no se toca: sin sub_agents y con padres no-LLM no hay destinos de
  transferencia; cada fase es una sesion aislada.
- Reintentos: los hace SOLO el backoff del orquestador (metricas + deteccion de cuota diaria),
  asi que el cliente genai se fija explicitamente a 1 intento.
- Modelo y temperatura por rol salen de `[agents]` del proyecto activo (temperatura baja
  por defecto: los roles son deterministas) y las instrucciones nombran sus areas reales.
"""

from google.adk.agents.llm_agent import LlmAgent
from google.genai import types

from .audit import record_tool_call, record_tool_error, record_tool_start
from .context import project
from .models import build_llm
from .probe import temperature_supported
from .prompts import (
    CODER_INSTRUCTION,
    EVIDENCE_INSTRUCTION,
    INTAKE_INSTRUCTION,
    PLANNER_INSTRUCTION,
    REVIEWER_INSTRUCTION,
    SECURITY_INSTRUCTION,
    TEST_ANALYST_INSTRUCTION,
    render,
)
from .providers import effective_temperature
from .tools import (
    inspect_git_diff,
    list_repo_files,
    read_repo_file,
    run_baseline_verification,
    run_security_scans_tool,
    write_product_file,
    write_run_evidence,
    write_test_file,
)

READ_TOOLS = [list_repo_files, read_repo_file]
ROLE_OF = {
    "IntakeAgent": "intake",
    "PlannerAgent": "planner",
    "CoderAgent": "coder",
    "TestAnalystAgent": "test_analyst",
    "ReviewerAgent": "reviewer",
    "SecurityAgent": "security",
    "EvidenceAgent": "evidence",
}


def _model(role: str):
    # Sin reintentos internos del SDK (genai attempts=1, anthropic/litellm 0): el backoff
    # del orquestador es el unico que reintenta (evita reintentos multiplicados).
    return build_llm(*project().config.agents.provider_model_for(role))


def model_settings(role: str) -> dict:
    """Proveedor, modelo y temperatura pedida/efectiva de un rol (va a la evidencia)."""
    agents = project().config.agents
    provider, model = agents.provider_model_for(role)
    requested = agents.temperature_for(role)
    effective = effective_temperature(
        provider, requested, temperature_supported(provider, model))
    return {"provider": provider, "model": model, "temperature_requested": requested,
            "temperature_effective": effective}


def _agent(name: str, description: str, instruction: str, tools: list, output_key: str):
    role = ROLE_OF[name]
    temperature = model_settings(role)["temperature_effective"]
    config = types.GenerateContentConfig(
        # None = no se envia (el probe detecto que el modelo no acepta temperatura)
        **({} if temperature is None else {"temperature": temperature})
    )
    return LlmAgent(
        name=name,
        model=_model(role),
        description=description,
        instruction=render(instruction, project()),
        tools=list(tools),
        output_key=output_key,
        generate_content_config=config,
        # Auditoria: cada llamada a tool queda en runs/<CR>/tool-calls.jsonl.
        before_tool_callback=record_tool_start,
        after_tool_callback=record_tool_call,
        on_tool_error_callback=record_tool_error,
    )


def make_intake_agent() -> LlmAgent:
    return _agent(
        "IntakeAgent",
        "Analyzes a Change Request and determines whether it is ready for planning.",
        INTAKE_INSTRUCTION,
        READ_TOOLS,
        "intake_report",
    )


def make_planner_agent() -> LlmAgent:
    return _agent(
        "PlannerAgent",
        "Creates an implementation plan without changing code.",
        # `?`: cada fase corre en una sesion ADK nueva (estado vacio); el intake ya llega en
        # el prompt. Sin `?` ADK lanza KeyError "Context variable not found".
        PLANNER_INSTRUCTION + "\n\nIntake report:\n{intake_report?}",
        READ_TOOLS,
        "implementation_plan",
    )


def make_coder_agent() -> LlmAgent:
    return _agent(
        "CoderAgent",
        "Implements an approved human plan with bounded write capability (src+tests only).",
        CODER_INSTRUCTION,
        [*READ_TOOLS, write_product_file],
        "coder_report",
    )


def make_test_analyst_agent() -> LlmAgent:
    return _agent(
        "TestAnalystAgent",
        "Finds test gaps, adds authorized tests (tests/product only), reads evidence.",
        TEST_ANALYST_INSTRUCTION,
        [*READ_TOOLS, run_baseline_verification, write_test_file],
        "test_analysis",
    )


def make_reviewer_agent() -> LlmAgent:
    return _agent(
        "ReviewerAgent",
        "Independent read-only software reviewer.",
        REVIEWER_INSTRUCTION,
        [*READ_TOOLS, inspect_git_diff],
        "review_report",
    )


def make_security_agent() -> LlmAgent:
    return _agent(
        "SecurityAgent",
        "Independent read-only security reviewer.",
        SECURITY_INSTRUCTION,
        [*READ_TOOLS, inspect_git_diff, run_security_scans_tool],
        "security_report",
    )


def make_evidence_agent() -> LlmAgent:
    return _agent(
        "EvidenceAgent",
        "Consolidates evidence without making the final release decision.",
        EVIDENCE_INSTRUCTION,
        [*READ_TOOLS, write_run_evidence],
        "evidence_summary",
    )


AGENT_FACTORIES = {
    "intake_agent": make_intake_agent,
    "planner_agent": make_planner_agent,
    "coder_agent": make_coder_agent,
    "test_analyst_agent": make_test_analyst_agent,
    "reviewer_agent": make_reviewer_agent,
    "security_agent": make_security_agent,
    "evidence_agent": make_evidence_agent,
}

# Solo introspeccion (contratos/permisos); el orquestador usa AGENT_FACTORIES.
# Se construyen con el proyecto por defecto y, si su proveedor no esta configurado (sin
# clave), con Gemini: importar este modulo nunca debe fallar por credenciales.
def _introspection_project():
    from .context import default_project
    from .project import Project, ProjectConfig
    from .roles import FactoryError

    base = default_project()
    try:
        for role in ROLE_OF.values():
            build_llm(*base.config.agents.provider_model_for(role))
        return base
    except FactoryError:
        return Project(base.root, ProjectConfig.model_validate(
            {**base.config.model_dump(), "agents": {}}))


def _build_introspection():
    from .context import use_project

    with use_project(_introspection_project()):
        return [factory() for factory in AGENT_FACTORIES.values()]


(intake_agent, planner_agent, coder_agent, test_analyst_agent, reviewer_agent, security_agent,
 evidence_agent) = _build_introspection()
