"""Contratos explícitos de cada agente (curso §8).

Cada contrato declara: role/goal, input context, allowed tools, denied tools/actions,
output schema, stop conditions y human escalation conditions. Son datos puros (sin
importar google-adk) para poder auditarlos y validarlos contra las tools REALES de
`agents.py` (tests/factory/test_agent_contracts.py). Los prompts viven en prompts.py;
los permisos se aplican en runtime por las tools acotadas de tools.py.
"""

from __future__ import annotations

REQUIRED_FIELDS = (
    "agent",
    "role",
    "context",
    "allow",
    "deny",
    "output",
    "stop_conditions",
    "escalation",
)

# Acciones que NINGUN agente puede ejecutar (AGENTS.md, §16 y §26 del curso).
GLOBAL_DENY = ("merge_pr", "modify_requirements", "weaken_tests", "expose_secrets")

CONTRACTS: dict[str, dict] = {
    "intake": {
        "agent": "IntakeAgent",
        "role": "Intake Agent — analiza CR, requisitos/AC afectados y detecta ambigüedad",
        "context": ["CHANGE_REQUEST.md", "REQUIREMENTS.md", "SPEC.md", "REPO_MAP.md"],
        "allow": ["list_repo_files", "read_repo_file"],
        "deny": ["edit_application_code", "write_product_file", *GLOBAL_DENY],
        "output": ["runs/<CR>/01-intake-report.md (PROCEED | HUMAN_CLARIFICATION_REQUIRED)"],
        "stop_conditions": ["ambiguous_requirement", "requirements_conflict"],
        "escalation": "Product Owner decide la interpretación (gate requirements)",
    },
    "planner": {
        "agent": "PlannerAgent",
        "role": "Planner Agent — plan técnico y archivos afectados; no escribe código",
        "context": ["01-intake-report.md", "ARCHITECTURE.md", "REPO_MAP.md", "selected context"],
        "allow": ["list_repo_files", "read_repo_file"],
        "deny": ["edit_application_code", "write_product_file", *GLOBAL_DENY],
        "output": ["runs/<CR>/03-implementation-plan.md (tabla Task | AC | files | tests)"],
        "stop_conditions": ["ambiguous_requirement", "invented_api_or_dependency"],
        "escalation": "Tech Lead aprueba/ajusta plan y alcance (gate plan); Coder bloqueado antes",
    },
    "coder": {
        "agent": "CoderAgent",
        "role": "Coder Agent — implementa solo el plan aprobado o los findings autorizados",
        "context": ["03-implementation-plan.md aprobado", "findings ACCEPTED/MODIFIED bloqueantes"],
        "allow": ["list_repo_files", "read_repo_file", "write_product_file"],
        "deny": ["write_run_evidence", "edit_requirements", "edit_factory", "self_approve",
                 *GLOBAL_DENY],
        "output": ["runs/<CR>/05-coder-report.md", "runs/<CR>/11-fix-report.md (si hubo fix)"],
        "stop_conditions": ["plan_not_approved", "write_denied_by_runtime"],
        "escalation": "Verification FAIL persistente (> MAX_FIX_ROUNDS) escala a humano",
    },
    "test_analyst": {
        "agent": "TestAnalystAgent",
        "role": "Test Analyst — gaps, negativos/boundary/regresión; agrega solo tests autorizados",
        "context": ["CHANGE_REQUEST.md", "REQUIREMENTS.md", "diff", "tests/product"],
        "allow": ["list_repo_files", "read_repo_file", "run_baseline_verification",
                  "write_test_file"],
        "deny": ["edit_application_code", "write_product_file", "determine_pass", *GLOBAL_DENY],
        "output": ["runs/<CR>/06-test-analysis.md", "tests/product/test_*.py"],
        "stop_conditions": ["insufficient_evidence", "requirements_conflict"],
        "escalation": "Tests que enmascaran defectos se reportan al Human Reviewer",
    },
    "reviewer": {
        "agent": "ReviewerAgent",
        "role": "Independent Code Reviewer (read-only, sesión propia, sin razonamiento del Coder)",
        "context": ["REQUIREMENTS.md", "SPEC.md", "ARCHITECTURE.md", "CHANGE_REQUEST.md",
                    "git diff", "tests / verification evidence"],
        "allow": ["list_repo_files", "read_repo_file", "inspect_git_diff"],
        "deny": ["edit_application_code", "write_product_file", "write_test_file",
                 "write_run_evidence", *GLOBAL_DENY],
        "output": ["runs/<CR>/08-review-report.md (RF-NN | MUST FIX/SHOULD FIX/OPTIONAL)",
                   "runs/<CR>/13-re-review.md (tras un fix)"],
        "stop_conditions": ["requirements_conflict", "insufficient_evidence"],
        "escalation": "Human Reviewer clasifica cada finding (gate review)",
    },
    "security": {
        "agent": "SecurityAgent",
        "role": "Security Reviewer (read-only + scanners; sin escritura de la aplicación)",
        "context": ["CHANGE_REQUEST.md", "git diff", "scanner evidence (SAST/SCA/secrets)"],
        "allow": ["list_repo_files", "read_repo_file", "inspect_git_diff",
                  "run_security_scans_tool"],
        "deny": ["edit_application_code", "write_product_file", "write_test_file",
                 "write_run_evidence", *GLOBAL_DENY],
        "output": ["runs/<CR>/09-security-report.md (SEC-NN | HIGH/MEDIUM/LOW)"],
        "stop_conditions": ["secret_detected", "insufficient_evidence"],
        "escalation": "HIGH/MUST FIX sin decisión humana bloquea el flujo",
    },
    "evidence": {
        "agent": "EvidenceAgent",
        "role": "Evidence Agent — consolida evidencia real; no decide el release",
        "context": ["artefactos del run", "factory-state.json"],
        "allow": ["list_repo_files", "read_repo_file", "write_run_evidence"],
        "deny": ["write_product_file", "fabricate_evidence", "decide_release", *GLOBAL_DENY],
        "output": ["runs/<CR>/14-evidence-summary.md", "traceability.md", "factory-run.json"],
        "stop_conditions": ["insufficient_evidence"],
        "escalation": "Release Manager decide READY FOR HUMAN MERGE / NOT READY (gate release)",
    },
}

MANDATORY_AGENTS = ("intake", "planner", "coder", "test_analyst", "reviewer", "security")
