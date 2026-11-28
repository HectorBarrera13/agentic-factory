import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Toasts } from "@/App";
import { Badge, ConfirmDialog, LoadingBlock } from "@/components/ui";
import { FactoryView } from "@/features/factory/FactoryView";
import { RunBoard } from "@/features/factory/RunBoard";
import { api, type CrInfo, type Meta, type ProjectConfig, type RunDetail, type RunState, type SessionInfo } from "@/lib/api";
import { AppProvider } from "@/lib/app-state";

const config = {
  agents: { provider: "gemini", model: "gemini-a", temperature: 0.2, roles: {} },
} as unknown as ProjectConfig;

const PO = { id: "p1", name: "Ana", roles: ["product owner"] };
const SESSION: SessionInfo = {
  project: { root: "C:/p/demo", name: "demo", configured: true, git: true, config },
  recent: [],
  profiles: [PO],
} as SessionInfo;

const META = {
  phases: [
    { id: "intake", label: "Intake" },
    { id: "requirements", label: "Gate requisitos", gate: "requirements" },
    { id: "plan", label: "Plan" },
  ],
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
} as unknown as Meta;

const state = (over: Partial<RunState> = {}): RunState => ({
  change_id: "CR-001",
  status: "INTAKE_PENDING",
  human_gates: { requirements: "PENDING", plan: "PENDING", review: "PENDING", release: "PENDING" },
  ...over,
});

const detail = (st: Partial<RunState> = {}, over: Partial<RunDetail> = {}): RunDetail => ({
  change_id: "CR-001",
  state: state(st),
  busy: false,
  outcome: null,
  artifacts: [],
  gate: null,
  findings: { items: [], pending: [] },
  worktree: null,
  ...over,
});

const crInfo = (over: Partial<CrInfo> = {}): CrInfo => ({
  change_id: "CR-001",
  file: "crs/CR-001.md",
  body: "# CR-001 — Demo\n\nantes",
  frozen: false,
  retryable: false,
  editable: true,
  archivable: false,
  deletable: true,
  reason: "",
  ...over,
});

function base() {
  vi.spyOn(api, "session").mockResolvedValue(SESSION);
  vi.spyOn(api, "meta").mockResolvedValue(META);
  vi.spyOn(api, "modelsInUse").mockResolvedValue({ items: [] });
  vi.spyOn(api, "activity").mockResolvedValue({ items: [], last: 0 });
  localStorage.setItem("factory.signer", JSON.stringify({ id: "p1", role: "product owner" }));
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/");
});
afterEach(() => vi.restoreAllMocks());

describe("primitivas", () => {
  it("ConfirmDialog no deja confirmar hasta teclear la palabra", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open onOpenChange={() => {}} title="Eliminar CR-007" word="CR-007" actionLabel="Eliminar" onConfirm={onConfirm} />);
    const action = screen.getByRole("button", { name: "Eliminar" });
    expect(action).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Escribe CR-007 para confirmar"), "cr-00");
    expect(action).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Escribe CR-007 para confirmar"), "7");
    expect(action).toBeEnabled();
    await userEvent.click(action);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("Badge lleva el texto completo en title y no lo parte", () => {
    render(<Badge>Intento {3}/{3}</Badge>);
    const badge = screen.getByTitle("Intento 3/3");
    expect(badge).toHaveTextContent("Intento 3/3");
  });

  it("LoadingBlock se anuncia como estado, no como texto suelto", () => {
    render(<LoadingBlock label="Cargando run…" />);
    expect(screen.getByRole("status", { name: "Cargando run…" })).toBeInTheDocument();
  });
});

describe("Change Requests: lista, archivados y eliminar", () => {
  const mockList = () => {
    base();
    vi.spyOn(api, "crs").mockImplementation(async (archived?: boolean) =>
      archived
        ? {
            items: [
              {
                change_id: "CR-009",
                file: "crs/archivados/CR-009.md",
                title: "Viejo",
                archived: true,
                archived_at: "2026-11-20T10:00:00Z",
                archived_by: "Ana (product owner)",
                status: "READY FOR HUMAN MERGE",
              },
            ],
          }
        : { items: [{ change_id: "CR-001", file: "crs/CR-001.md", title: "Demo con un título largo", has_run: false }] },
    );
    vi.spyOn(api, "runs").mockResolvedValue({ items: [] });
    vi.spyOn(api, "cr").mockResolvedValue(crInfo());
  };

  it("filtra Activos / Archivados y Restaurar llama al servidor", async () => {
    mockList();
    const restore = vi.spyOn(api, "restoreCr").mockResolvedValue({ change_id: "CR-009" });
    render(<AppProvider><FactoryView /></AppProvider>);
    expect(await screen.findByText("Demo con un título largo")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Archivados/ }));
    const item = (await screen.findByText("Viejo")).closest("[data-archived]") as HTMLElement;
    expect(within(item).getByText(/Ana \(product owner\)/)).toBeInTheDocument();
    expect(within(item).getByTitle("Listo para merge humano")).toBeInTheDocument();
    await userEvent.click(within(item).getByRole("button", { name: "Restaurar CR-009" }));
    expect(restore).toHaveBeenCalledWith("CR-009");
  });

  it("un CR sin run se elimina tecleando su ID", async () => {
    mockList();
    const del = vi.spyOn(api, "deleteCr").mockResolvedValue({ change_id: "CR-001", deleted: ["crs/CR-001.md"] });
    window.history.replaceState(null, "", "/#/factory/CR-001");
    render(<AppProvider><FactoryView /></AppProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Más acciones de CR-001" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Eliminar/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/La rama git se conserva/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Escribe CR-001 para confirmar"), "CR-001");
    await userEvent.click(within(dialog).getByRole("button", { name: "Eliminar para siempre" }));
    expect(del).toHaveBeenCalledWith("CR-001", "CR-001", { profile_id: "p1", role: "product owner" });
  });

  it("un CR congelado no se edita y dice por qué", async () => {
    mockList();
    vi.spyOn(api, "cr").mockResolvedValue(crInfo({ editable: false, frozen: true, deletable: false, reason: "el intake ya corrio" }));
    window.history.replaceState(null, "", "/#/factory/CR-001");
    render(<AppProvider><FactoryView /></AppProvider>);
    expect(await screen.findByTitle("el intake ya corrio")).toHaveTextContent("congelado");
    expect(screen.queryByRole("button", { name: /Editar/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Más acciones de CR-001" })).not.toBeInTheDocument();
  });
});

describe("Run: editar antes del intake, archivar y eliminar al terminar", () => {
  it("Editar CR aparece antes del primer Avanzar y guarda", async () => {
    base();
    vi.spyOn(api, "run").mockResolvedValue(detail());
    vi.spyOn(api, "cr").mockResolvedValue(crInfo());
    const update = vi.spyOn(api, "updateCr").mockResolvedValue({ file: "crs/CR-001.md" });
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    await userEvent.click(await screen.findByRole("button", { name: /Editar CR/ }));
    const box = await screen.findByLabelText("Contenido del CR");
    await waitFor(() => expect(box).toHaveValue("# CR-001 — Demo\n\nantes"));
    await userEvent.type(box, " y después");
    await userEvent.click(screen.getByRole("button", { name: /Guardar/ }));
    expect(update).toHaveBeenCalledWith("CR-001", "# CR-001 — Demo\n\nantes y después");
  });

  it("sin Editar CR cuando el intake ya corrió", async () => {
    base();
    vi.spyOn(api, "run").mockResolvedValue(detail({ status: "PLAN_PENDING" }));
    vi.spyOn(api, "cr").mockResolvedValue(crInfo({ editable: false, deletable: false, reason: "congelado" }));
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    expect(await screen.findByRole("button", { name: /Avanzar/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Editar CR/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Archivar/ })).not.toBeInTheDocument();
  });

  it("un run terminado se archiva y el toast deja Deshacer", async () => {
    base();
    vi.spyOn(api, "run").mockResolvedValue(detail({ status: "READY FOR HUMAN MERGE" }));
    vi.spyOn(api, "cr").mockResolvedValue(crInfo({ editable: false, archivable: true, deletable: true }));
    const archive = vi.spyOn(api, "archiveCr").mockResolvedValue({ change_id: "CR-001", archived_at: "x", worktree_removed: true });
    const onRemoved = vi.fn();
    const onRestore = vi.fn();
    render(
      <AppProvider>
        <RunBoard changeId="CR-001" onRemoved={onRemoved} onRestore={onRestore} />
        <Toasts />
      </AppProvider>,
    );
    const btn = await screen.findByRole("button", { name: /Archivar/ });
    await waitFor(() => expect(btn).toBeEnabled());
    await userEvent.click(btn);
    expect(archive).toHaveBeenCalledWith("CR-001", { profile_id: "p1", role: "product owner" });
    expect(onRemoved).toHaveBeenCalled();
    const toast = await screen.findByRole("alert");
    expect(toast).toHaveTextContent(/archivado \(worktree quitado\)/);
    await userEvent.click(within(toast).getByRole("button", { name: "Deshacer" }));
    expect(onRestore).toHaveBeenCalledWith("CR-001");
  });

  it("Eliminar en un run terminado pide el ID y conserva la rama", async () => {
    base();
    vi.spyOn(api, "run").mockResolvedValue(detail({ status: "NOT READY" }, { worktree: "C:/wt/CR-001" }));
    vi.spyOn(api, "cr").mockResolvedValue(crInfo({ editable: false, archivable: true, deletable: true }));
    const del = vi.spyOn(api, "deleteCr").mockResolvedValue({ change_id: "CR-001", deleted: ["a", "b"] });
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    await userEvent.click(await screen.findByRole("button", { name: "Más acciones de CR-001" }));
    expect(screen.getByRole("menuitem", { name: /Quitar worktree/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("menuitem", { name: /Eliminar/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("menuitem", { name: /Eliminar/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/carpeta del run con toda su evidencia/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Escribe CR-001 para confirmar"), "CR-001");
    await userEvent.click(within(dialog).getByRole("button", { name: "Eliminar para siempre" }));
    expect(del).toHaveBeenCalledWith("CR-001", "CR-001", { profile_id: "p1", role: "product owner" });
  });

  it("con la decisión registrada, Ahora dice que falta pulsar Avanzar", async () => {
    base();
    vi.spyOn(api, "run").mockResolvedValue(
      detail(
        { status: "REQUIREMENTS_GATE", awaiting_gate: "requirements" },
        {
          gate: {
            name: "requirements",
            question: "¿Requisitos claros?",
            artifacts: [],
            roles: ["product owner"],
            findings_roles: [],
            recorded: { decision: "APPROVED", reason: "ok", recorded_by: "Ana (product owner)", recorded_at: "x" },
          },
        },
      ),
    );
    vi.spyOn(api, "cr").mockResolvedValue(crInfo({ editable: false }));
    render(<AppProvider><RunBoard changeId="CR-001" /></AppProvider>);
    expect(await screen.findByText("Decisión registrada · pulsa Avanzar")).toBeInTheDocument();
    expect(screen.queryByText(/Esperando a/)).not.toBeInTheDocument();
  });
});

