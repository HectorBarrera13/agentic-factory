import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FindingsTable } from "@/features/factory/FindingsTable";
import { GateCard } from "@/features/factory/GateCard";
import { PhaseStepper } from "@/features/factory/PhaseStepper";
import type { Finding, Phase, RunState } from "@/lib/api";
import type { Signer } from "@/lib/app-state";
import { currentPhaseId } from "@/lib/phases";

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

const state = (over: Partial<RunState>): RunState => ({
  change_id: "CR-001",
  status: "INTAKE_PENDING",
  human_gates: { requirements: "PENDING", plan: "PENDING", review: "PENDING", release: "PENDING" },
  ...over,
});

const ana = { id: "p1", name: "Ana", roles: ["tech lead", "reviewer"] };
const signer = (role: string): Signer => ({ profile: ana, role });

const DECISIONS = ["APPROVED", "MODIFIED", "REJECTED", "DEFERRED"];
const VERDICTS = ["ACCEPTED", "REJECTED", "MODIFIED", "DEFERRED"];

describe("PhaseStepper", () => {
  it("marca el gate abierto como esperando y lo anterior como hecho", () => {
    render(<PhaseStepper phases={PHASES} state={state({ status: "PLAN_PENDING", awaiting_gate: "plan" })} busy={false} />);
    const items = screen.getAllByRole("listitem");
    expect(items[3]).toHaveAttribute("data-state", "waiting");
    expect(items[0]).toHaveAttribute("data-state", "done");
    expect(items[2]).toHaveAttribute("data-state", "done");
    expect(items[4]).toHaveAttribute("data-state", "pending");
  });

  it("un run escalado muestra la fase actual como fallida", () => {
    render(<PhaseStepper phases={PHASES} state={state({ status: "ESCALATED" })} busy={false} />);
    expect(screen.getAllByRole("listitem")[9]).toHaveAttribute("data-state", "failed");
  });

  it("listo para merge = todas hechas", () => {
    render(<PhaseStepper phases={PHASES} state={state({ status: "READY FOR HUMAN MERGE" })} busy={false} />);
    for (const li of screen.getAllByRole("listitem")) expect(li).toHaveAttribute("data-state", "done");
  });

  it("mapea estados del motor a fases", () => {
    expect(currentPhaseId(state({ status: "REVIEW_PENDING", awaiting_gate: "review" }))).toBe("review_gate");
    expect(currentPhaseId(state({ status: "VERIFY_PENDING" }))).toBe("verify");
    expect(currentPhaseId(state({ status: "EVIDENCE_PENDING", awaiting_gate: "release" }))).toBe("release");
  });
});

describe("GateCard", () => {
  const gate = { name: "plan", question: "¿Apruebas este plan?", artifacts: ["03-implementation-plan.md"], roles: ["tech lead"], findings_roles: [], recorded: null };

  it("exige motivo y firma con el perfil y rol activos", async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    render(<GateCard gate={gate} decisions={DECISIONS} signer={signer("tech lead")} pendingFindings={0} onOpenArtifact={() => undefined} onDecide={onDecide} />);
    const submit = screen.getByRole("button", { name: /registrar decisión/i });
    expect(submit).toBeDisabled();
    expect(screen.getByText("Ana (Tech Lead)")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Motivo"), "Plan acotado al CR");
    await userEvent.selectOptions(screen.getByLabelText("Decisión"), "MODIFIED");
    await userEvent.click(submit);
    expect(onDecide).toHaveBeenCalledWith({ decision: "MODIFIED", reason: "Plan acotado al CR" });
  });

  it("bloquea si el rol activo no esta autorizado para el gate", async () => {
    render(<GateCard gate={gate} decisions={DECISIONS} signer={signer("reviewer")} pendingFindings={0} onOpenArtifact={() => undefined} onDecide={vi.fn()} />);
    expect(screen.getByText(/no puede firmar este gate/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Motivo"), "x");
    expect(screen.getByRole("button", { name: /registrar decisión/i })).toBeDisabled();
  });

  it("el gate review no se decide con findings sin clasificar", async () => {
    const review = { ...gate, name: "review", roles: ["reviewer"] };
    render(<GateCard gate={review} decisions={DECISIONS} signer={signer("reviewer")} pendingFindings={2} onOpenArtifact={() => undefined} onDecide={vi.fn()} />);
    expect(screen.getByText(/2 finding\(s\) sin clasificar/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Motivo"), "ok");
    expect(screen.getByRole("button", { name: /registrar decisión/i })).toBeDisabled();
  });

  it("sin perfil pide elegir uno", () => {
    render(<GateCard gate={gate} decisions={DECISIONS} signer={{ profile: null, role: "" }} pendingFindings={0} onOpenArtifact={() => undefined} onDecide={vi.fn()} />);
    expect(screen.getByText(/elige un perfil/i)).toBeInTheDocument();
  });

  it("con decision registrada muestra la firma y permite cambiarla", async () => {
    const recorded = { decision: "APPROVED", reason: "Plan ok", recorded_by: "Ana (Tech Lead)", recorded_at: "2026-11-27T10:00:00Z" };
    render(<GateCard gate={{ ...gate, recorded }} decisions={DECISIONS} signer={signer("tech lead")} pendingFindings={0} onOpenArtifact={() => undefined} onDecide={vi.fn()} />);
    expect(screen.getByText(/decisión registrada/i)).toBeInTheDocument();
    expect(screen.getByText("Ana (Tech Lead)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /registrar decisión/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /cambiar decisión/i }));
    expect(screen.getByRole("button", { name: /registrar decisión/i })).toBeInTheDocument();
  });

  it("abre los artefactos del gate", async () => {
    const onOpen = vi.fn();
    render(<GateCard gate={gate} decisions={DECISIONS} signer={signer("tech lead")} pendingFindings={0} onOpenArtifact={onOpen} onDecide={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /03-implementation-plan\.md/ }));
    expect(onOpen).toHaveBeenCalledWith("03-implementation-plan.md");
  });
});

describe("FindingsTable", () => {
  const items: Finding[] = [
    { id: "RF-01", severity: "MUST FIX", blocking: true, decision: null, excerpt: "Falta validar entrada" },
    { id: "SEC-01", severity: "MEDIUM", blocking: false, decision: "DEFERRED", excerpt: "" },
  ];

  it("envia solo las clasificaciones cambiadas", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<FindingsTable items={items} pending={["RF-01"]} verdicts={VERDICTS} roles={["reviewer"]} signer={signer("reviewer")} editable onSave={onSave} />);
    expect(screen.getByText("1 de 2 sin clasificar")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: /guardar clasificación/i });
    expect(save).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Clasificar RF-01"), "ACCEPTED");
    await userEvent.click(screen.getByRole("button", { name: /guardar clasificación \(1\)/i }));
    expect(onSave).toHaveBeenCalledWith({ "RF-01": "ACCEPTED" });
  });

  it("RESOLVED solo se ofrece para un finding ya corregido en una ronda anterior", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const rows: Finding[] = [
      { id: "HR-01", severity: "MUST FIX", blocking: true, decision: null, excerpt: "", fixed_before: true },
      { id: "RF-02", severity: "MUST FIX", blocking: true, decision: null, excerpt: "" },
    ];
    render(<FindingsTable items={rows} pending={["HR-01", "RF-02"]} verdicts={[...VERDICTS, "RESOLVED"]} roles={["reviewer"]} signer={signer("reviewer")} editable onSave={onSave} />);
    const resolvedIn = (id: string) => Array.from((screen.getByLabelText(`Clasificar ${id}`) as HTMLSelectElement).options).some((o) => o.value === "RESOLVED");
    expect(resolvedIn("HR-01")).toBe(true);
    expect(resolvedIn("RF-02")).toBe(false);
    await userEvent.selectOptions(screen.getByLabelText("Clasificar HR-01"), "RESOLVED");
    await userEvent.click(screen.getByRole("button", { name: /guardar clasificación \(1\)/i }));
    expect(onSave).toHaveBeenCalledWith({ "HR-01": "RESOLVED" });
  });

  it("sin el rol de findings no permite clasificar", () => {
    render(<FindingsTable items={items} pending={["RF-01"]} verdicts={VERDICTS} roles={["human reviewer"]} signer={signer("tech lead")} editable onSave={vi.fn()} />);
    expect(screen.getByText(/tu rol no puede clasificar/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Clasificar RF-01")).toBeDisabled();
  });

  it("un SHOULD FIX aceptado se puede marcar para corregir, con su motivo", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const should: Finding[] = [{ id: "RF-02", severity: "SHOULD FIX", blocking: false, decision: null, excerpt: "sync.js sin usar", source: "reviewer" }];
    render(<FindingsTable items={should} pending={["RF-02"]} verdicts={VERDICTS} roles={["reviewer"]} signer={signer("reviewer")} editable onSave={onSave} />);
    expect(screen.queryByLabelText("Corregir RF-02 en este ciclo")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Clasificar RF-02"), "ACCEPTED");
    expect(screen.getByText("Queda registrado, no cambia código")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Corregir RF-02 en este ciclo"));
    expect(screen.getByText("El Coder lo corrige en la ronda de fix")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ver RF-02" }));
    await userEvent.type(screen.getByLabelText("Motivo de RF-02"), "cargar sync.js");
    await userEvent.click(screen.getByRole("button", { name: /guardar clasificación \(1\)/i }));
    expect(onSave).toHaveBeenCalledWith({ "RF-02": { decision: "ACCEPTED", reason: "cargar sync.js", blocking: true } });
  });

  it("el Human Reviewer añade un finding propio", async () => {
    const onAdd = vi.fn().mockResolvedValue(undefined);
    render(<FindingsTable items={[]} pending={[]} verdicts={VERDICTS} roles={["reviewer"]} signer={signer("reviewer")} editable onSave={vi.fn()} onAdd={onAdd} />);
    expect(screen.getByText("Sin findings en esta ronda")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /añadir finding/i }));
    const form = screen.getByRole("form", { name: "Añadir finding" });
    expect(within(form).getByRole("button", { name: /^añadir$/i })).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("Título del finding"), "Eco al emisor");
    await userEvent.type(within(form).getByLabelText("AC del finding"), "AC-01");
    await userEvent.click(within(form).getByRole("button", { name: /^añadir$/i }));
    expect(onAdd).toHaveBeenCalledWith({ severity: "MUST FIX", title: "Eco al emisor", detail: "", ac: "AC-01", blocking: false });
    expect(screen.queryByRole("form", { name: "Añadir finding" })).not.toBeInTheDocument();
  });

  it("sin edicion muestra el motivo y la marca de corregir", async () => {
    const done: Finding[] = [{ id: "RF-03", severity: "SHOULD FIX", blocking: false, decision: "ACCEPTED", excerpt: "x", reason: "hazlo ya", human_blocking: true }];
    render(<FindingsTable items={done} pending={[]} verdicts={VERDICTS} roles={["reviewer"]} signer={signer("reviewer")} editable={false} onSave={vi.fn()} />);
    expect(screen.getByText(/corregir en este ciclo/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ver RF-03" }));
    expect(screen.getByText("hazlo ya")).toBeInTheDocument();
  });

  it("muestra el extracto del finding al expandir", async () => {
    render(<FindingsTable items={items} pending={[]} verdicts={VERDICTS} roles={["reviewer"]} signer={signer("reviewer")} editable={false} onSave={vi.fn()} />);
    const row = screen.getByText("RF-01").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Ver RF-01" }));
    expect(screen.getByText("Falta validar entrada")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /guardar clasificación/i })).not.toBeInTheDocument();
  });
});
