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
- Temperatura baja: los roles son deterministas (planes, revisiones, findings).
"""

import os

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.google_llm import Gemini
from google.genai import types

from .audit import record_tool_call, record_tool_error
from .prompts import (
    CODER_INSTRUCTION,
    EVIDENCE_INSTRUCTION,
    INTAKE_INSTRUCTION,
    PLANNER_INSTRUCTION,
    REVIEWER_INSTRUCTION,
    SECURITY_INSTRUCTION,
    TEST_ANALYST_INSTRUCTION,
)
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

GEMINI_MODEL = os.getenv("FACTORY_MODEL", "gemini-3.8-flash")
TEMPERATURE = 0.2
READ_TOOLS = [list_repo_files, read_repo_file]


def _model() -> Gemini:
    # attempts=1: sin reintentos internos de google-genai; el backoff del orquestador es
    # el unico que reintenta (evita reintentos multiplicados y cuota gastada a ciegas).
    return Gemini(model=GEMINI_MODEL, retry_options=types.HttpRetryOptions(attempts=1))


def _agent(name: str, description: str, instruction: str, tools: list, output_key: str):
    return LlmAgent(
        name=name,
        model=_model(),
        description=description,
        instruction=instruction,
        tools=list(tools),
        output_key=output_key,
        generate_content_config=types.GenerateContentConfig(temperature=TEMPERATURE),
        # Auditoria: cada llamada a tool queda en runs/<CR>/tool-calls.jsonl.
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
intake_agent = make_intake_agent()
planner_agent = make_planner_agent()
coder_agent = make_coder_agent()
test_analyst_agent = make_test_analyst_agent()
reviewer_agent = make_reviewer_agent()
security_agent = make_security_agent()
evidence_agent = make_evidence_agent()
