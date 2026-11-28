import * as React from "react";
import { api, type ActivityEvent, type Phase, type RunState } from "./api";
import { stepStates } from "./phases";
import type { RoleId } from "./roles";

// ---------------------------------------------------------------- carga incremental
/** Actividad del run: lee solo lo nuevo (`after`) cada vez que el SSE avisa (`tick`). */
export function useActivity(changeId: string, tick: number, attempt = 1) {
  const [items, setItems] = React.useState<ActivityEvent[]>([]);
  const last = React.useRef(0);
  const key = `${changeId}#${attempt}`;
  const shown = React.useRef(key);

  React.useEffect(() => {
    // otro run u otro intento: la secuencia empieza de nuevo
    if (shown.current !== key) {
      shown.current = key;
      last.current = 0;
      setItems([]);
    }
    let alive = true;
    api
      .activity(changeId, last.current)
      .then((r) => {
        if (!alive) return;
        if (r.last < last.current) {
          // el archivo se reinicio (Reintentar): recargar entero
          last.current = 0;
          setItems([]);
          return;
        }
        if (r.items.length) {
          last.current = r.items[r.items.length - 1].seq;
          setItems((prev) => [...prev, ...r.items.filter((e) => e.seq > (prev[prev.length - 1]?.seq ?? 0))]);
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [changeId, tick, key]);

  return items;
}

/** Reloj que avanza cada segundo solo mientras `active` (temporizadores en vivo). */
export function useNow(active: boolean): number {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

export function formatDuration(ms: number | undefined | null): string {
  if (ms === undefined || ms === null || ms < 0) return "";
  const s = Math.floor(ms / 1000);
  if (s < 1) return `${ms} ms`;
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}:${String(s % 60).padStart(2, "0")}`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

// ---------------------------------------------------------------- modelo del diagrama
export type NodeStatus = "idle" | "running" | "done" | "failed" | "waiting" | "paused";
export type NodeKind = "agent" | "check" | "gate" | "end";

export interface FlowNodeDef {
  id: string;
  kind: NodeKind;
  role: RoleId;
  label: string;
  /** fases del motor (activity.phase) que trabajan en este nodo */
  phases: string[];
  gate?: string;
  artifact?: string;
  /** columna y fila (0 = centro, -1 arriba, 1 abajo) del diagrama */
  col: number;
  row: number;
  /** id de la fase del stepper (fallback para runs sin actividad) */
  step: string;
}

export const FLOW: FlowNodeDef[] = [
  { id: "intake", kind: "agent", role: "intake", label: "Intake", phases: ["intake"], artifact: "01-intake-report.md", col: 0, row: 0, step: "intake" },
  { id: "g_requirements", kind: "gate", role: "human", label: "Gate requisitos", phases: [], gate: "requirements", artifact: "02-human-requirement-decision.md", col: 1, row: 0, step: "requirements" },
  { id: "planner", kind: "agent", role: "planner", label: "Planner", phases: ["plan"], artifact: "03-implementation-plan.md", col: 2, row: 0, step: "plan" },
  { id: "g_plan", kind: "gate", role: "human", label: "Gate plan", phases: [], gate: "plan", artifact: "04-human-plan-approval.md", col: 3, row: 0, step: "plan_gate" },
  { id: "coder", kind: "agent", role: "coder", label: "Coder", phases: ["implement", "fix"], artifact: "05-coder-report.md", col: 4, row: -1, step: "implement" },
  { id: "test_analyst", kind: "agent", role: "test_analyst", label: "Test Analyst", phases: ["test_analysis"], artifact: "06-test-analysis.md", col: 4, row: 1, step: "implement" },
  { id: "verify", kind: "check", role: "verify", label: "Verificación", phases: ["verify"], artifact: "07-verification.txt", col: 5, row: 0, step: "verify" },
  { id: "reviewer", kind: "agent", role: "reviewer", label: "Reviewer", phases: ["review"], artifact: "08-review-report.md", col: 6, row: -1, step: "review" },
  { id: "security", kind: "agent", role: "security", label: "Security", phases: ["security"], artifact: "09-security-report.md", col: 6, row: 1, step: "review" },
  { id: "g_review", kind: "gate", role: "human", label: "Gate review", phases: [], gate: "review", col: 7, row: 0, step: "review_gate" },
  { id: "evidence", kind: "check", role: "evidence", label: "Evidencia", phases: ["evidence"], artifact: "14-evidence-summary.md", col: 8, row: 0, step: "evidence" },
  { id: "g_release", kind: "gate", role: "human", label: "Gate release", phases: [], gate: "release", artifact: "15-release-decision.json", col: 9, row: 0, step: "release" },
  { id: "end", kind: "end", role: "human", label: "Merge humano", phases: [], col: 10, row: 0, step: "release" },
];

export const FLOW_EDGES: [string, string][] = [
  ["intake", "g_requirements"],
  ["g_requirements", "planner"],
  ["planner", "g_plan"],
  ["g_plan", "coder"],
  ["coder", "test_analyst"],
  ["test_analyst", "verify"],
  ["verify", "reviewer"],
  ["reviewer", "security"],
  ["security", "g_review"],
  ["g_review", "evidence"],
  ["evidence", "g_release"],
  ["g_release", "end"],
];

export interface VerifyCommand {
  command: string;
  status: "running" | "ok" | "failed";
  duration_ms?: number;
}

export interface FlowNode extends FlowNodeDef {
  status: NodeStatus;
  /** ISO del inicio del trabajo en curso */
  since?: string;
  duration_ms?: number;
  /** veces que trabajo este nodo (rondas de fix incluidas) */
  runs: number;
  provider?: string;
  model?: string;
  tool?: { name: string; target?: string; running: boolean; ok?: boolean };
  commands?: VerifyCommand[];
  /** gate: roles que pueden decidir y decision registrada */
  roles?: string[];
  decision?: string;
  by?: string;
  message?: string;
}

export interface FlowLoop {
  fix: "verify" | "review";
  n: number;
  cap?: number;
  /** el Coder esta corrigiendo por este motivo ahora mismo */
  active: boolean;
}

export interface Flow {
  nodes: FlowNode[];
  byId: Record<string, FlowNode>;
  loops: FlowLoop[];
  /** nodo que trabaja (o espera) ahora */
  focus: string | null;
}

const GATE_NODE: Record<string, string> = {
  requirements: "g_requirements",
  plan: "g_plan",
  review: "g_review",
  release: "g_release",
};

function nodeForPhase(phase: string | undefined): FlowNodeDef | undefined {
  return phase ? FLOW.find((n) => n.phases.includes(phase)) : undefined;
}

/** Estado del flujo a partir del estado persistido y la actividad en vivo (puro). */
export function deriveFlow(state: RunState, events: ActivityEvent[], phases: Phase[], busy: boolean): Flow {
  const nodes: FlowNode[] = FLOW.map((def) => ({ ...def, status: "idle", runs: 0 }));
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n])) as Record<string, FlowNode>;
  const loops: Record<string, FlowLoop> = {};
  let lastTouched: string | null = null;
  let lastFix: "verify" | "review" | null = null;

  if (!events.length) {
    // runs anteriores a la actividad en vivo: el stepper sabe lo esencial
    const steps = stepStates(phases, state);
    const stepOf = Object.fromEntries(phases.map((p, i) => [p.id, steps[i]]));
    for (const n of nodes) {
      const s = stepOf[n.step];
      n.status = s === "done" ? "done" : s === "failed" ? "failed" : s === "waiting" ? "waiting" : s === "current" ? (busy ? "running" : "idle") : "idle";
    }
    if (state.status === "READY FOR HUMAN MERGE") byId.end.status = "done";
  }

  for (const e of events) {
    if (e.kind === "phase_start" || e.kind === "phase_end" || e.kind === "phase_error") {
      const def = nodeForPhase(e.phase);
      if (!def) continue;
      const n = byId[def.id];
      lastTouched = n.id;
      if (e.kind === "phase_start") {
        n.status = "running";
        n.since = e.ts;
        n.runs += 1;
        n.provider = e.provider ?? n.provider;
        n.model = e.model ?? n.model;
        n.tool = undefined;
        n.message = undefined;
        if (n.kind === "check" && n.id === "verify") n.commands = [];
        if (e.phase === "fix" && lastFix && loops[lastFix]) loops[lastFix].active = true;
        if (e.phase === "implement" || (n.id !== "coder" && lastFix)) {
          for (const l of Object.values(loops)) l.active = false;
        }
      } else if (e.kind === "phase_end") {
        n.status = e.ok === false ? "failed" : "done";
        n.duration_ms = e.duration_ms;
        n.since = undefined;
        if (e.ok === false) n.message = `${e.failed ?? 0} fallido(s) de ${e.tests ?? 0}`;
        if (n.tool) n.tool = { ...n.tool, running: false };
        if (e.phase === "fix") for (const l of Object.values(loops)) l.active = false;
      } else {
        n.status = "failed";
        n.duration_ms = e.duration_ms;
        n.since = undefined;
        n.message = e.message;
      }
    } else if (e.kind === "tool_start" || e.kind === "tool_end") {
      const def = nodeForPhase(e.phase);
      if (!def) continue;
      byId[def.id].tool = { name: e.tool ?? "", target: e.target, running: e.kind === "tool_start", ok: e.ok };
    } else if (e.kind === "verify_cmd_start" || e.kind === "verify_cmd_end") {
      const v = byId.verify;
      v.commands = v.commands ?? [];
      const existing = v.commands.find((c) => c.command === e.command);
      const next: VerifyCommand = {
        command: e.command ?? "",
        status: e.kind === "verify_cmd_start" ? "running" : e.ok ? "ok" : "failed",
        duration_ms: e.duration_ms,
      };
      if (existing) Object.assign(existing, next);
      else v.commands.push(next);
    } else if (e.kind === "scan_start" || e.kind === "scan_end") {
      // los escaneres deterministas corren al cerrar Security: el nodo sigue trabajando
      const sec = byId.security;
      const start = e.kind === "scan_start";
      sec.tool = { name: "escáneres", running: start, ok: start ? undefined : !e.blocked };
      if (start) {
        sec.status = "running";
        sec.since = e.ts;
      } else {
        sec.status = "done";
        if (sec.since) sec.duration_ms = (sec.duration_ms ?? 0) + Math.max(0, Date.parse(e.ts) - Date.parse(sec.since));
        sec.since = undefined;
      }
      lastTouched = sec.id;
    } else if (e.kind === "gate_open" && e.gate) {
      const n = byId[GATE_NODE[e.gate]];
      if (!n) continue;
      n.status = "waiting";
      n.since = e.ts;
      n.roles = e.roles;
      n.decision = undefined;
      n.by = undefined;
      lastTouched = n.id;
    } else if (e.kind === "gate_decision" && e.gate) {
      const n = byId[GATE_NODE[e.gate]];
      if (!n) continue;
      n.decision = e.decision;
      n.by = e.by;
      n.since = undefined;
      n.status = e.decision === "APPROVED" || e.decision === "MODIFIED" ? "done" : "failed";
      lastTouched = n.id;
    } else if (e.kind === "fix_round" && e.fix) {
      loops[e.fix] = { fix: e.fix, n: e.n ?? 1, cap: e.cap, active: true };
      lastFix = e.fix;
    } else if (e.kind === "escalated") {
      const target = lastTouched ? byId[lastTouched] : null;
      if (target && !(state.status === "ESCALATED" && state.awaiting_gate)) {
        target.status = "failed";
        target.message = e.reason;
      }
    } else if (e.kind === "done") {
      byId.end.status = e.status === "READY FOR HUMAN MERGE" ? "done" : "failed";
    }
  }

  // el estado persistido manda sobre lo que la actividad no puede saber
  if (state.awaiting_gate) {
    const n = byId[GATE_NODE[state.awaiting_gate]];
    if (n && n.status !== "failed") n.status = "waiting";
  }
  if (state.status === "READY FOR HUMAN MERGE") byId.end.status = "done";
  if (state.status === "NOT READY") byId.end.status = "failed";
  if (state.paused && !busy) {
    const def = FLOW.find((n) => n.step === STATUS_STEP[state.paused!.status]);
    if (def) byId[def.id].status = "paused";
  }
  if (!busy) {
    // sin hilo activo nada puede estar "trabajando" (corte inesperado o pausa)
    for (const n of nodes) if (n.status === "running") n.status = state.paused ? "paused" : "idle";
  }

  const running = nodes.filter((n) => n.status === "running");
  const waiting = nodes.find((n) => n.status === "waiting");
  const focus = running.length ? running[running.length - 1].id : waiting?.id ?? null;
  return { nodes, byId, loops: Object.values(loops), focus };
}

const STATUS_STEP: Record<string, string> = {
  INTAKE_PENDING: "intake",
  PLAN_PENDING: "plan",
  IMPLEMENT_PENDING: "implement",
  VERIFY_PENDING: "verify",
  REVIEW_PENDING: "review",
  EVIDENCE_PENDING: "evidence",
};

// ---------------------------------------------------------------- textos
const TOOL_VERB: Record<string, string> = {
  read_repo_file: "leyendo",
  list_repo_files: "listando",
  search_repo: "buscando",
  write_product_file: "escribiendo",
  write_test_file: "escribiendo test",
  inspect_git_diff: "revisando el diff",
  run_security_scans_tool: "corriendo escáneres",
  escáneres: "corriendo escáneres",
};

export function toolPhrase(tool: { name: string; target?: string; running: boolean; ok?: boolean } | undefined): string {
  if (!tool) return "";
  if (tool.name === "escáneres" && !tool.running) {
    return tool.ok === false ? "escáneres: bloquean el release" : "escáneres: sin bloqueos";
  }
  const verb = TOOL_VERB[tool.name] ?? tool.name;
  const what = tool.target ? ` ${tool.target}` : "";
  return tool.running ? `${verb}${what}…` : `último: ${verb}${what}`;
}

export function elapsedSince(iso: string | undefined, now: number): number | undefined {
  if (!iso) return undefined;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? undefined : Math.max(0, now - t);
}
