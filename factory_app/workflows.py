"""Pipelines para el playground (`adk web` / agents-cli). El orquestador NO los usa: corre
cada fase con una instancia propia creada por `agents.AGENT_FACTORIES`.

Instancias propias (no las de modulo de agents.py): un agente ADK solo puede tener un padre.
"""

import warnings

from .agents import (
    make_intake_agent,
    make_planner_agent,
    make_reviewer_agent,
    make_security_agent,
)

with warnings.catch_warnings():
    # ADK 2.x depreca Sequential/ParallelAgent en favor de Workflow; se mantienen porque
    # son la raiz que espera el playground y no cambian el comportamiento del motor.
    warnings.simplefilter("ignore", DeprecationWarning)
    from google.adk.agents import ParallelAgent, SequentialAgent

    planning_pipeline = SequentialAgent(
        name="PlanningPipeline",
        description="Read-only Intake -> Planning phase.",
        sub_agents=[make_intake_agent(), make_planner_agent()],
    )

    independent_review_pipeline = ParallelAgent(
        name="IndependentReviewPipeline",
        description="Runs software review and security review independently.",
        sub_agents=[make_reviewer_agent(), make_security_agent()],
    )
