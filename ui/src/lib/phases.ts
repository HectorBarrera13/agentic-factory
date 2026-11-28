import type { Phase, RunState } from "./api";

export type StepState = "done" | "current" | "waiting" | "pending" | "failed";

// Estado del motor -> fase del stepper (el orden de fases es fijo, ver /api/meta).
const STATUS_PHASE: Record<string, string> = {
  INTAKE_PENDING: "intake",
  REQUIREMENTS_GATE: "requirements",
  PLAN_PENDING: "plan",
  IMPLEMENT_PENDING: "implement",
  VERIFY_PENDING: "verify",
  REVIEW_PENDING: "review",
  EVIDENCE_PENDING: "evidence",
};
const GATE_PHASE: Record<string, string> = {
  requirements: "requirements",
  plan: "plan_gate",
  review: "review_gate",
  release: "release",
};

export const TERMINAL = ["READY FOR HUMAN MERGE", "NOT READY", "ESCALATED"];

export function currentPhaseId(state: RunState): string {
  if (state.awaiting_gate) return GATE_PHASE[state.awaiting_gate] ?? "intake";
  if (TERMINAL.includes(state.status)) return "release";
  return STATUS_PHASE[state.status] ?? "intake";
}

export function stepStates(phases: Phase[], state: RunState): StepState[] {
  const current = phases.findIndex((p) => p.id === currentPhaseId(state));
  return phases.map((phase, i) => {
    if (state.status === "READY FOR HUMAN MERGE") return "done";
    if (i < current) return "done";
    if (i > current) return "pending";
    if (state.status === "ESCALATED" || state.status === "NOT READY") return "failed";
    if (phase.gate && state.awaiting_gate) return "waiting";
    return "current";
  });
}

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

export function statusTone(status: string, awaiting?: string): Tone {
  if (status === "READY FOR HUMAN MERGE") return "success";
  if (status === "ESCALATED" || status === "NOT READY") return "danger";
  if (awaiting) return "warning";
  return "info";
}

/** Etiqueta corta para listas estrechas (la completa va en el title). */
export function statusShort(status: string, awaiting?: string): string {
  if (awaiting) return `Gate ${awaiting}`;
  const short: Record<string, string> = {
    INTAKE_PENDING: "Intake",
    REQUIREMENTS_GATE: "Requisitos",
    PLAN_PENDING: "Plan",
    IMPLEMENT_PENDING: "Implementación",
    VERIFY_PENDING: "Verificación",
    REVIEW_PENDING: "Review",
    EVIDENCE_PENDING: "Evidencia",
    "READY FOR HUMAN MERGE": "Listo",
    "NOT READY": "No listo",
    ESCALATED: "Escalado",
  };
  return short[status] ?? status;
}

export function statusLabel(status: string, awaiting?: string): string {
  if (awaiting) return `Esperando gate ${awaiting}`;
  const labels: Record<string, string> = {
    INTAKE_PENDING: "Intake pendiente",
    REQUIREMENTS_GATE: "Gate requisitos",
    PLAN_PENDING: "Plan pendiente",
    IMPLEMENT_PENDING: "Implementación pendiente",
    VERIFY_PENDING: "Verificación pendiente",
    REVIEW_PENDING: "Review pendiente",
    EVIDENCE_PENDING: "Evidencia pendiente",
    "READY FOR HUMAN MERGE": "Listo para merge humano",
    "NOT READY": "No listo",
    ESCALATED: "Escalado",
  };
  return labels[status] ?? status;
}
