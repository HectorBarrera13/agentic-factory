import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunBoard } from "@/features/factory/RunBoard";
import { OpenProjectDialog } from "@/features/project/OpenProject";
import {
  api,
  type Meta,
  type ModelInUse,
  type ProjectConfig,
  type RunDetail,
  type RunState,
  type SessionInfo,
} from "@/lib/api";
import { AppProvider, useApp } from "@/lib/app-state";
import { formatRoute, parseHash } from "@/lib/route";

const config = {
  agents: { provider: "gemini", model: "gemini-a", temperature: 0.2, roles: {} },
} as unknown as ProjectConfig;

const SESSION: SessionInfo = {
  project: { root: "C:/p/demo", name: "demo", configured: true, git: true, config },
  recent: [],
  profiles: [],
};

const META = {
  phases: [{ id: "intake", label: "Intake" }, { id: "plan", label: "Plan" }],
  gates: [],
  decisions: [],
  verdicts: [],
  agent_roles: [],
  gate_keys: [],
  verify_kinds: [],
  languages: [],
  role_catalog: [],
  providers: [],
  defaults: config,
} as Meta;

const runState = (over: Partial<RunState> = {}): RunState => ({
  change_id: "CR-001",
  status: "INTAKE_PENDING",
  human_gates: { requirements: "PENDING", plan: "PENDING", review: "PENDING", release: "PENDING" },
  ...over,
});

const detail = (over: Partial<RunDetail> = {}, st: Partial<RunState> = {}): RunDetail => ({
  change_id: "CR-001",
  state: runState(st),
  busy: false,
  outcome: null,
  artifacts: [],
  gate: null,
  findings: { items: [], pending: [] },
  worktree: null,
  ...over,
});

const model = (status: "OK" | "QUOTA" | null): ModelInUse => ({
  provider: "gemini",
  model: "gemini-a",
  roles: ["intake", "planner"],
  probe: status ? { provider: "gemini", model: "gemini-a", status, message: "429", temperature_supported: true, at: 1, seconds: 1 } : null,
  env_override: false,
  configured_model: null,
});

function mockBase(run: RunDetail, models: ModelInUse[]) {
  vi.spyOn(api, "session").mockResolvedValue(SESSION);
  vi.spyOn(api, "meta").mockResolvedValue(META);
  vi.spyOn(api, "run").mockResolvedValue(run);
  vi.spyOn(api, "modelsInUse").mockResolvedValue({ items: models });
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/");
});
afterEach(() => vi.restoreAllMocks());

describe("ruteo por hash", () => {
  it("ida y vuelta de las rutas", () => {
    for (const hash of ["#/factory", "#/factory/CR-04", "#/settings/providers", "#/code?root=user&path=app%2Fx.py"]) {
      expect(formatRoute(parseHash(hash))).toBe(hash);
    }
    expect(parseHash("#/code?root=run%3ACR-1&path=a%2Fb.py").file).toEqual({ root: "run:CR-1", path: "a/b.py" });
    expect(parseHash("#/nada").view).toBe("factory");
  });

  it("al recargar conserva vista, run y pestaña; navegar escribe la URL", async () => {
    vi.spyOn(api, "session").mockResolvedValue(SESSION);
    vi.spyOn(api, "meta").mockResolvedValue(META);
    window.history.replaceState(null, "", "/?token=x#/settings/providers");
    function Probe() {
      const { view, settingsTab, selectedRun, setView, setSelectedRun } = useApp();
      return (
        <div>
          <span data-testid="where">{`${view}|${settingsTab}|${selectedRun}`}</span>
          <button
            onClick={() => {
              setView("factory");
              setSelectedRun("CR-07");
            }}
          >
            ir
          </button>
        </div>
      );
    }
    render(<AppProvider><Probe /></AppProvider>);
    expect(screen.getByTestId("where")).toHaveTextContent("settings|providers|null");
    await userEvent.click(screen.getByRole("button", { name: "ir" }));
    await waitFor(() => expect(window.location.hash).toBe("#/factory/CR-07"));
    expect(window.location.search).toBe("?token=x");
  });
});

describe("RunBoard", () => {
  it("el modo por defecto es el modelo real; la simulacion va segunda", async () => {
    mockBase(detail(), [model("OK")]);
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    const select = await screen.findByLabelText("Modo de ejecución");
    const options = [...(select as HTMLSelectElement).options].map((o) => o.textContent);
    expect(options[0]).toMatch(/^Modelo real \(gemini\/gemini-a\)/);
    expect(options[1]).toMatch(/Simulación/);
    expect(select).toHaveValue("adk");
  });

  it("avisa y pide confirmar antes de avanzar con un modelo que no paso la prueba", async () => {
    mockBase(detail(), [model("QUOTA")]);
    const advance = vi.spyOn(api, "advance").mockResolvedValue({ kind: "running", at: 1 });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    expect(await screen.findByText("Un modelo en uso no pasó la prueba")).toBeInTheDocument();
    expect(screen.getByText("sin cuota")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Avanzar/ }));
    expect(confirm).toHaveBeenCalled();
    expect(advance).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: /Avanzar/ }));
    expect(advance).toHaveBeenCalledWith("CR-001", "adk");
    // en simulacion no hay modelo: ni aviso ni confirmacion
    await userEvent.selectOptions(screen.getByLabelText("Modo de ejecución"), "fake");
    expect(screen.queryByText("Un modelo en uso no pasó la prueba")).not.toBeInTheDocument();
    expect(localStorage.getItem("factory.mode")).toBe("fake");
  });

  it("FACTORY_MODEL del entorno no pasa en silencio", async () => {
    mockBase(detail(), [{ ...model("OK"), model: "gemini-env", env_override: true, configured_model: "gemini-a" }]);
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    expect(await screen.findByText("FACTORY_MODEL del entorno reemplaza el modelo")).toBeInTheDocument();
  });

  it("un run en curso se puede pausar", async () => {
    mockBase(detail({ busy: true, outcome: { kind: "running", at: 1 } }), [model("OK")]);
    const pause = vi.spyOn(api, "pause").mockResolvedValue({ kind: "running", pausing: true, at: 1 });
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    await userEvent.click(await screen.findByRole("button", { name: /Pausar/ }));
    expect(pause).toHaveBeenCalledWith("CR-001");
  });

  it("mientras corta muestra Pausando…", async () => {
    mockBase(detail({ busy: true, outcome: { kind: "running", pausing: true, at: 1 } }), [model("OK")]);
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    expect(await screen.findByRole("button", { name: /Pausando/ })).toBeDisabled();
  });

  it("un run pausado muestra donde quedo y se reanuda", async () => {
    const paused = { at: "x", status: "IMPLEMENT_PENDING", reason: "pausado durante implement", worktree_reset: true };
    mockBase(detail({ outcome: { kind: "paused", at: 1 } }, { status: "IMPLEMENT_PENDING", paused }), [model("OK")]);
    const advance = vi.spyOn(api, "advance").mockResolvedValue({ kind: "running", at: 1 });
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    expect(await screen.findByText(/Run pausado en/)).toBeInTheDocument();
    expect(screen.getByText(/se descartaron/)).toBeInTheDocument();
    expect(screen.getByText(/Pausada en/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pausar/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Reanudar/ }));
    expect(advance).toHaveBeenCalledWith("CR-001", "adk");
  });
});

describe("Abrir proyecto", () => {
  it("Examinar… usa el explorador del sistema y analiza la carpeta elegida", async () => {
    vi.spyOn(api, "session").mockResolvedValue(SESSION);
    vi.spyOn(api, "meta").mockResolvedValue(META);
    vi.spyOn(api, "browse").mockResolvedValue({ path: "C:/p/otro" });
    const detect = vi.spyOn(api, "detect").mockResolvedValue({ config, notes: [], has_config: true });
    vi.spyOn(api, "openProject").mockResolvedValue(SESSION.project!);
    render(<AppProvider><OpenProjectDialog open onOpenChange={vi.fn()} /></AppProvider>);
    await userEvent.click(screen.getByRole("button", { name: /Examinar/ }));
    await waitFor(() => expect(detect).toHaveBeenCalledWith("C:/p/otro"));
  });

  it("cancelar el explorador no cambia nada", async () => {
    vi.spyOn(api, "session").mockResolvedValue(SESSION);
    vi.spyOn(api, "meta").mockResolvedValue(META);
    vi.spyOn(api, "browse").mockResolvedValue({ path: null });
    const detect = vi.spyOn(api, "detect");
    render(<AppProvider><OpenProjectDialog open onOpenChange={vi.fn()} /></AppProvider>);
    await userEvent.click(screen.getByRole("button", { name: /Examinar/ }));
    await waitFor(() => expect(api.browse).toHaveBeenCalled());
    expect(detect).not.toHaveBeenCalled();
  });
});
