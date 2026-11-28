import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProviderLogo } from "@/components/ProviderLogo";
import { ActivityLog, groupActivity } from "@/features/factory/ActivityLog";
import { FlowDiagram } from "@/features/factory/FlowDiagram";
import { NowStrip } from "@/features/factory/NowStrip";
import type { ActivityEvent, Phase, RunState } from "@/lib/api";
import { deriveFlow, toolPhrase } from "@/lib/activity";

const PHASES: Phase[] = [
  { id: "intake", label: "Intake" },
  { id: "requirements", label: "Gate requisitos", gate: "requirements" },
  { id: "plan", label: "Plan" },
  { id: "plan_gate", label: "Gate plan", gate: "plan" },
  { id: "implement", label: "Implementación" },
  { id: "verify", label: "Verificación" },
  { id: "review", label: "Review + Security" },
  { id: "review_gate", label: "Gate review", gate: "review" },
  { id: "evidence", label: "Evidencia" },
  { id: "release", label: "Gate release", gate: "release" },
];

const state = (over: Partial<RunState> = {}): RunState => ({
  change_id: "CR-001",
  status: "IMPLEMENT_PENDING",
  human_gates: { requirements: "APPROVED", plan: "APPROVED", review: "PENDING", release: "PENDING" },
  ...over,
});

let seq = 0;
const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();
const ev = (kind: string, fields: Partial<ActivityEvent> = {}): ActivityEvent => ({ seq: ++seq, ts: ago(5), kind, ...fields });

/** Un run que ya paso intake, plan y sus gates, y tiene al Coder trabajando. */
function coderWorking(): ActivityEvent[] {
  return [
    ev("phase_start", { phase: "intake", role: "intake", provider: "gemini", model: "gemini-a" }),
    ev("phase_end", { phase: "intake", role: "intake", duration_ms: 3200 }),
    ev("gate_open", { gate: "requirements", roles: ["product owner"] }),
    ev("gate_decision", { gate: "requirements", decision: "APPROVED", by: "Ana (product owner)" }),
    ev("phase_start", { phase: "plan", role: "planner", provider: "gemini", model: "gemini-a" }),
    ev("phase_end", { phase: "plan", role: "planner", duration_ms: 4100 }),
    ev("gate_open", { gate: "plan", roles: ["tech lead"] }),
    ev("gate_decision", { gate: "plan", decision: "APPROVED", by: "Ana (tech lead)" }),
    { ...ev("phase_start", { phase: "implement", role: "coder", provider: "anthropic", model: "claude-x" }), ts: ago(72) },
    ev("tool_start", { phase: "implement", tool: "write_product_file", target: "campusflow/service.py" }),
  ];
}

beforeEach(() => {
  seq = 0;
  localStorage.clear();
});

describe("deriveFlow", () => {
  it("el nodo que trabaja trae rol, modelo y tool en curso; lo anterior queda hecho", () => {
    const flow = deriveFlow(state(), coderWorking(), PHASES, true);
    const coder = flow.byId.coder;
    expect(coder.status).toBe("running");
    expect(coder.provider).toBe("anthropic");
    expect(coder.model).toBe("claude-x");
    expect(coder.tool).toMatchObject({ name: "write_product_file", target: "campusflow/service.py", running: true });
    expect(flow.focus).toBe("coder");
    expect(flow.byId.intake).toMatchObject({ status: "done", duration_ms: 3200 });
    expect(flow.byId.g_plan).toMatchObject({ status: "done", decision: "APPROVED", by: "Ana (tech lead)" });
    expect(flow.byId.verify.status).toBe("idle");
  });

  it("un gate abierto espera al rol que corresponde", () => {
    const events = coderWorking().slice(0, 3);
    const flow = deriveFlow(state({ status: "REQUIREMENTS_GATE", awaiting_gate: "requirements" }), events, PHASES, false);
    expect(flow.byId.g_requirements).toMatchObject({ status: "waiting", roles: ["product owner"] });
    expect(flow.focus).toBe("g_requirements");
  });

  it("las rondas de fix se cuentan y marcan la vuelta al Coder", () => {
    const events = [
      ...coderWorking(),
      ev("tool_end", { phase: "implement", tool: "write_product_file", ok: true }),
      ev("phase_end", { phase: "implement", role: "coder", duration_ms: 1000 }),
      ev("phase_start", { phase: "verify", role: "verify" }),
      ev("verify_cmd_start", { phase: "verify", command: "pytest" }),
      ev("verify_cmd_end", { command: "pytest", ok: false, returncode: 1, duration_ms: 900 }),
      ev("phase_end", { phase: "verify", role: "verify", ok: false, tests: 4, failed: 1 }),
      ev("fix_round", { fix: "verify", n: 1, cap: 3 }),
      ev("phase_start", { phase: "fix", role: "coder", provider: "anthropic", model: "claude-x" }),
    ];
    const flow = deriveFlow(state({ status: "VERIFY_PENDING" }), events, PHASES, true);
    expect(flow.loops).toEqual([{ fix: "verify", n: 1, cap: 3, active: true }]);
    expect(flow.byId.coder).toMatchObject({ status: "running", runs: 2 });
    expect(flow.byId.verify.status).toBe("failed");
    expect(flow.byId.verify.commands).toEqual([{ command: "pytest", status: "failed", duration_ms: 900 }]);
  });

  it("una fase con error queda roja con su motivo", () => {
    const events = [...coderWorking(), ev("phase_error", { phase: "implement", role: "coder", message: "429 sin cuota" })];
    const flow = deriveFlow(state(), events, PHASES, false);
    expect(flow.byId.coder).toMatchObject({ status: "failed", message: "429 sin cuota" });
  });

  it("pausado: la fase cortada se ve pausada, no trabajando", () => {
    const paused = { at: "x", status: "IMPLEMENT_PENDING", reason: "pausa", worktree_reset: true };
    const flow = deriveFlow(state({ paused }), [...coderWorking(), ev("paused", { reason: "pausa" })], PHASES, false);
    expect(flow.byId.coder.status).toBe("paused");
    expect(flow.nodes.some((n) => n.status === "running")).toBe(false);
  });

  it("sin hilo activo nada queda trabajando aunque falte el fin de fase", () => {
    const flow = deriveFlow(state(), coderWorking(), PHASES, false);
    expect(flow.byId.coder.status).toBe("idle");
  });

  it("runs sin actividad (anteriores) usan el estado persistido", () => {
    const flow = deriveFlow(state({ status: "PLAN_PENDING", awaiting_gate: "plan" }), [], PHASES, false);
    expect(flow.byId.intake.status).toBe("done");
    expect(flow.byId.planner.status).toBe("done");
    expect(flow.byId.g_plan.status).toBe("waiting");
    expect(flow.byId.coder.status).toBe("idle");
    const ready = deriveFlow(state({ status: "READY FOR HUMAN MERGE" }), [], PHASES, false);
    expect(ready.nodes.every((n) => n.status === "done")).toBe(true);
  });

  it("el escalado deja en rojo lo ultimo que se toco y el fin como no listo", () => {
    const events = [...coderWorking().slice(0, 7), ev("gate_decision", { gate: "plan", decision: "REJECTED", by: "Ana" }), ev("escalated", { reason: "gate plan: REJECTED" }), ev("done", { status: "NOT READY" })];
    const flow = deriveFlow(state({ status: "NOT READY" }), events, PHASES, false);
    expect(flow.byId.g_plan.status).toBe("failed");
    expect(flow.byId.end.status).toBe("failed");
  });

  it("describe la tool en curso en palabras", () => {
    expect(toolPhrase({ name: "write_product_file", target: "a.py", running: true })).toBe("escribiendo a.py…");
    expect(toolPhrase({ name: "read_repo_file", target: "b.py", running: false })).toBe("último: leyendo b.py");
  });
});

describe("FlowDiagram", () => {
  const renderFlow = (events: ActivityEvent[], busy = true, onOpen = vi.fn()) =>
    render(
      <FlowDiagram
        flow={deriveFlow(state(), events, PHASES, busy)}
        phases={PHASES}
        state={state()}
        busy={busy}
        artifacts={["01-intake-report.md"]}
        onOpenArtifact={onOpen}
      />,
    );

  it("muestra quien trabaja con estado accesible, modelo y tiempo", () => {
    renderFlow(coderWorking());
    const coder = screen.getByLabelText("Coder trabajando…");
    expect(coder).toHaveAttribute("data-status", "running");
    expect(coder).toHaveTextContent("claude-x");
    expect(coder).toHaveTextContent("escribiendo campusflow/service.py…");
    expect(coder).toHaveTextContent("1:1"); // ~72 s
    expect(screen.getByLabelText("Intake listo")).toBeInTheDocument();
    expect(screen.getByLabelText(/Gate plan listo: APPROVED · Ana \(tech lead\)/)).toBeInTheDocument();
    expect(document.querySelector('[data-edge="g_plan-coder"]')).toHaveAttribute("data-state", "active");
    expect(document.querySelector('[data-edge="coder-test_analyst"]')).toHaveAttribute("data-state", "idle");
  });

  it("la vuelta de fix aparece con su contador", () => {
    const events = [
      ...coderWorking(),
      ev("phase_end", { phase: "implement", role: "coder" }),
      ev("phase_start", { phase: "verify", role: "verify" }),
      ev("phase_end", { phase: "verify", role: "verify", ok: false, tests: 2, failed: 1 }),
      ev("fix_round", { fix: "verify", n: 2, cap: 3 }),
      ev("phase_start", { phase: "fix", role: "coder" }),
    ];
    renderFlow(events);
    expect(document.querySelector('[data-loop="verify"]')).toHaveAttribute("data-active");
    expect(screen.getByText("fix 2/3")).toBeInTheDocument();
  });

  it("un nodo con artefacto lo abre al hacer click", async () => {
    const onOpen = vi.fn();
    renderFlow(coderWorking(), true, onOpen);
    await userEvent.click(screen.getByRole("button", { name: "Intake listo" }));
    expect(onOpen).toHaveBeenCalledWith("01-intake-report.md");
  });

  it("contraer a la tira de fases se recuerda", async () => {
    const { unmount } = renderFlow(coderWorking());
    await userEvent.click(screen.getByRole("button", { name: /Contraer/ }));
    expect(screen.getByRole("list", { name: "Fases del run" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Coder trabajando…")).not.toBeInTheDocument();
    expect(localStorage.getItem("factory.flow.compact")).toBe("1");
    unmount();
    renderFlow(coderWorking());
    expect(screen.getByRole("list", { name: "Fases del run" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Ver diagrama/ }));
    expect(screen.getByLabelText("Coder trabajando…")).toBeInTheDocument();
  });
});

describe("NowStrip", () => {
  it("dice quien trabaja, con que modelo, en que y desde cuando", () => {
    const flow = deriveFlow(state(), coderWorking(), PHASES, true);
    render(<NowStrip flow={flow} busy live={{ phase: "implement", role: "coder", provider: "anthropic", model: "claude-x", since: ago(72) }} />);
    const strip = screen.getByRole("status", { name: "Ahora: Coder trabajando" });
    expect(strip).toHaveTextContent("claude-x");
    expect(strip).toHaveTextContent("escribiendo campusflow/service.py…");
    expect(strip).toHaveTextContent("1:1");
    expect(screen.getByRole("img", { name: "Anthropic" })).toBeInTheDocument();
  });

  it("en un gate dice a quien espera; sin trabajo no muestra nada", () => {
    const events = coderWorking().slice(0, 3);
    const flow = deriveFlow(state({ status: "REQUIREMENTS_GATE", awaiting_gate: "requirements" }), events, PHASES, false);
    const { unmount } = render(<NowStrip flow={flow} busy={false} />);
    expect(screen.getByRole("status")).toHaveTextContent("Esperando a product owner");
    unmount();
    const idle = deriveFlow(state({ status: "READY FOR HUMAN MERGE" }), [], PHASES, false);
    const { container } = render(<NowStrip flow={idle} busy={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ActivityLog", () => {
  it("agrupa por fase con sus tools y deja gates y fix como eventos sueltos", () => {
    const events = [
      ...coderWorking(),
      ev("tool_end", { phase: "implement", tool: "write_product_file", ok: true, duration_ms: 40 }),
      ev("phase_end", { phase: "implement", role: "coder", duration_ms: 72000 }),
      ev("fix_round", { fix: "review", n: 1, cap: 2, findings: 3 }),
    ];
    const groups = groupActivity(events);
    const coder = groups.find((g) => g.start?.phase === "implement")!;
    expect(coder.end?.duration_ms).toBe(72000);
    expect(coder.items.map((e) => e.kind)).toEqual(["tool_start", "tool_end"]);
    render(<ActivityLog events={events} busy={false} />);
    expect(screen.getByText(/abierto: espera a product owner/)).toBeInTheDocument();
    expect(screen.getByText(/Ronda de fix 1\/2 por review \(3 finding\(s\)\)/)).toBeInTheDocument();
    // una linea por llamada (inicio + fin fusionados)
    expect(screen.getAllByText("escribiendo campusflow/service.py")).toHaveLength(1);
  });

  it("solo agentes oculta tools y comandos", async () => {
    render(<ActivityLog events={[...coderWorking()]} busy />);
    expect(screen.getByText("escribiendo campusflow/service.py…")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "Solo agentes" }));
    expect(screen.queryByText("escribiendo campusflow/service.py…")).not.toBeInTheDocument();
    expect(screen.getAllByText("Coder").length).toBeGreaterThan(0);
  });
});

describe("ProviderLogo", () => {
  it("usa el logo real de cada proveedor y un icono generico para el resto", () => {
    render(
      <div>
        {["gemini", "anthropic", "openai", "ollama", "openai_compatible"].map((p) => (
          <ProviderLogo key={p} provider={p} />
        ))}
      </div>,
    );
    for (const name of ["Google Gemini", "Anthropic", "OpenAI", "Ollama"]) {
      const svg = screen.getByRole("img", { name });
      expect(svg.querySelector("path")?.getAttribute("d")?.length).toBeGreaterThan(40);
    }
    expect(screen.getByRole("img", { name: "Compatible con OpenAI" })).toHaveAttribute("data-provider", "openai_compatible");
  });
});

describe("escáneres de seguridad", () => {
  const security = () => [
    ev("phase_start", { phase: "security", role: "security", provider: "gemini", model: "gemini-a" }),
    ev("phase_end", { phase: "security", role: "security", duration_ms: 2000 }),
    { ...ev("scan_start", { phase: "security", role: "security" }), ts: ago(30) },
  ];

  it("Security sigue trabajando mientras corren los escáneres", () => {
    const flow = deriveFlow(state({ status: "REVIEW_PENDING" }), security(), PHASES, true);
    expect(flow.byId.security).toMatchObject({ status: "running", tool: { name: "escáneres", running: true } });
    expect(flow.focus).toBe("security");
    const end = [...security(), { ...ev("scan_end", { phase: "security", role: "security", blocked: false }), ts: ago(0) }];
    const done = deriveFlow(state({ status: "REVIEW_PENDING" }), end, PHASES, true);
    expect(done.byId.security.status).toBe("done");
    expect(done.byId.security.duration_ms).toBeGreaterThanOrEqual(29000);
  });

  it("en la actividad los escáneres van dentro del bloque de Security", () => {
    const events = [...security(), { ...ev("scan_end", { phase: "security", role: "security", blocked: true }), ts: ago(0) }];
    const groups = groupActivity(events);
    expect(groups).toHaveLength(1);
    expect(groups[0].items.map((e) => e.kind)).toEqual(["scan_start", "scan_end"]);
    render(<ActivityLog events={events} busy={false} />);
    expect(screen.getByText("escáneres de seguridad: bloquean el release")).toBeInTheDocument();
  });
});
