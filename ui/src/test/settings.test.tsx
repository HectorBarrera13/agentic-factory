import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelPicker, resetModelCache } from "@/features/settings/ModelPicker";
import { ProvidersPanel } from "@/features/settings/ProvidersPanel";
import { RoleChips } from "@/features/settings/SettingsView";
import { TemperatureField } from "@/features/settings/TemperatureField";
import { api, type Probes, type ProviderInfo, type ProviderStatus } from "@/lib/api";

const none: ProviderStatus = { has_key: false, key_source: "none", last4: null, url: null, url_source: "none" };

const provider = (over: Partial<ProviderInfo>): ProviderInfo => ({
  id: "anthropic",
  label: "Anthropic",
  description: "Modelos Claude",
  needs_key: true,
  needs_url: false,
  key_optional: false,
  temperature_range: [0, 1],
  default_url: null,
  status: none,
  ...over,
});

const PROVIDERS: ProviderInfo[] = [
  provider({ id: "gemini", label: "Google Gemini", temperature_range: [0, 2], status: { ...none, has_key: true, key_source: "env", last4: "9xyz" } }),
  provider({}),
  provider({ id: "ollama", label: "Ollama (local)", needs_key: false, needs_url: true, temperature_range: [0, 2], default_url: "http://localhost:11434", status: { ...none, url: "http://localhost:11434" } }),
];

const noProbes: Probes = { items: {}, running: [], version: 0 };

beforeEach(() => resetModelCache());
afterEach(() => vi.restoreAllMocks());

describe("ProvidersPanel", () => {
  it("primero se elige el proveedor; guarda la clave y muestra si funciona", async () => {
    const save = vi.spyOn(api, "saveProvider").mockResolvedValue({
      status: { ...none, has_key: true, key_source: "file", last4: "1234" },
      test: { ok: false, kind: "INVALID_KEY", message: "clave invalida o sin permiso", models: 0 },
    });
    const onChanged = vi.fn();
    render(<ProvidersPanel providers={PROVIDERS} onChanged={onChanged} />);
    await userEvent.click(screen.getByRole("button", { name: "Anthropic" }));
    const submit = screen.getByRole("button", { name: /guardar y probar/i });
    expect(submit).toBeDisabled();
    const input = screen.getByLabelText("API key de Anthropic");
    expect(input).toHaveAttribute("type", "password");
    await userEvent.type(input, "sk-ant-test-1234");
    await userEvent.click(submit);
    expect(save).toHaveBeenCalledWith("anthropic", { api_key: "sk-ant-test-1234" });  // pragma: allowlist secret
    expect(await screen.findByText(/clave inválida/i)).toBeInTheDocument();
    expect(input).toHaveValue(""); // la clave no queda en pantalla
    expect(onChanged).toHaveBeenCalled();
  });

  it("una clave de variable de entorno no se edita ni se borra desde la UI", () => {
    render(<ProvidersPanel providers={PROVIDERS} onChanged={vi.fn()} />);
    expect(screen.getByLabelText("API key de Google Gemini")).toBeDisabled();
    expect(screen.getByText(/desde variable de entorno/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /borrar clave/i })).not.toBeInTheDocument();
  });

  it("los locales piden URL y prueban la conexion", async () => {
    const test = vi.spyOn(api, "testProvider").mockResolvedValue({ ok: true, kind: "OK", message: "3 modelos", models: 3 });
    render(<ProvidersPanel providers={PROVIDERS} onChanged={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Ollama (local)" }));
    expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("URL de Ollama (local)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /probar conexión/i }));
    expect(test).toHaveBeenCalledWith("ollama");
    expect(await screen.findByText(/Conectado · 3 modelos/)).toBeInTheDocument();
  });
});

describe("ModelPicker", () => {
  it("ofrece la lista del proveedor y avisa si el modelo no aparece", async () => {
    vi.spyOn(api, "providerModels").mockResolvedValue({
      items: [{ id: "claude-a", label: "Claude A" }, { id: "claude-b", label: "claude-b" }],
      error: null,
    });
    const { container } = render(
      <ModelPicker label="Modelo por defecto" provider="anthropic" value="claude-z" onChange={vi.fn()} probes={noProbes} />,
    );
    await waitFor(() => expect(container.querySelectorAll("datalist option")).toHaveLength(2));
    expect(screen.getByText(/no aparece en la lista/)).toBeInTheDocument();
    expect(screen.getByText("sin probar")).toBeInTheDocument();
  });

  it("sin lista permite escribir el nombre a mano", async () => {
    vi.spyOn(api, "providerModels").mockResolvedValue({ items: [], error: { kind: "NO_KEY", message: "falta la API key" } });
    const onChange = vi.fn();
    render(<ModelPicker label="Modelo por defecto" provider="openai" value="" onChange={onChange} probes={noProbes} />);
    expect(await screen.findByText(/puedes escribir el nombre a mano/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Modelo por defecto"), "g");
    expect(onChange).toHaveBeenCalledWith("g");
  });

  it("prueba el modelo y muestra el resultado del probe", async () => {
    vi.spyOn(api, "providerModels").mockResolvedValue({ items: [{ id: "llama3", label: "llama3" }], error: null });
    const probe = vi.spyOn(api, "probe").mockResolvedValue({
      provider: "ollama", model: "llama3", status: "NO_TOOLS", message: "respondio sin llamar a la tool",
      temperature_supported: true, at: 1, seconds: 1,
    });
    const probes: Probes = {
      items: { "ollama:llama3": { provider: "ollama", model: "llama3", status: "NO_ACCESS", message: "403", temperature_supported: null, at: 1, seconds: 1 } },
      running: [],
      version: 1,
    };
    const { rerender } = render(<ModelPicker label="Modelo coder" provider="ollama" value="llama3" onChange={vi.fn()} probes={probes} />);
    expect(screen.getByText("sin acceso en tu plan")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Probar Modelo coder" }));
    expect(probe).toHaveBeenCalledWith("ollama", "llama3");
    rerender(<ModelPicker label="Modelo coder" provider="ollama" value="llama3" onChange={vi.fn()} probes={{ ...probes, running: ["ollama:llama3"] }} />);
    expect(screen.getByText(/probando/)).toBeInTheDocument();
  });

  it("un rol que hereda prueba el modelo heredado", async () => {
    vi.spyOn(api, "providerModels").mockResolvedValue({ items: [], error: null });
    const probe = vi.spyOn(api, "probe").mockResolvedValue({
      provider: "gemini", model: "gemini-x", status: "OK", message: "", temperature_supported: true, at: 1, seconds: 1,
    });
    render(<ModelPicker label="Modelo reviewer" provider="gemini" value="" placeholder="gemini-x" onChange={vi.fn()} probes={noProbes} />);
    await userEvent.click(screen.getByRole("button", { name: "Probar Modelo reviewer" }));
    expect(probe).toHaveBeenCalledWith("gemini", "gemini-x");
  });
});

describe("TemperatureField", () => {
  it("usa el rango del proveedor y avisa el valor que se aplicara", () => {
    render(<TemperatureField label="Temperatura" value={1.5} onChange={vi.fn()} range={[0, 1]} />);
    expect(screen.getByLabelText("Temperatura")).toHaveAttribute("max", "1");
    expect(screen.getByText(/se aplicará 1/)).toBeInTheDocument();
  });

  it("avisa cuando el modelo no acepta temperatura", () => {
    render(<TemperatureField label="Temperatura" value={0.2} onChange={vi.fn()} range={[0, 2]} supported={false} />);
    expect(screen.getByText(/no acepta temperatura/)).toBeInTheDocument();
  });

  it("atajos y heredar", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<TemperatureField label="T coder" value={null} inherited={0.2} onChange={onChange} range={[0, 2]} />);
    expect(screen.getByText("(hereda)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Equilibrado/ }));
    expect(onChange).toHaveBeenLastCalledWith(0.7);
    rerender(<TemperatureField label="T coder" value={0.7} inherited={0.2} onChange={onChange} range={[0, 2]} />);
    await userEvent.click(screen.getByRole("button", { name: "Heredar" }));
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});

describe("RoleChips", () => {
  const catalog = ["product owner", "tech lead", "release manager"];

  it("solo ofrece el catalogo, sin texto libre, en orden del catalogo", async () => {
    const onChange = vi.fn();
    render(<RoleChips label="Roles release" catalog={catalog} value={["release manager"]} onChange={onChange} />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Product Owner", "Tech Lead", "Release Manager"]);
    await userEvent.click(screen.getByRole("button", { name: "Tech Lead" }));
    expect(onChange).toHaveBeenCalledWith(["tech lead", "release manager"]);
  });

  it("un gate no puede quedarse sin roles", () => {
    render(<RoleChips label="Roles plan" catalog={catalog} value={["tech lead"]} onChange={vi.fn()} keepOne />);
    expect(screen.getByRole("button", { name: "Tech Lead" })).toBeDisabled();
  });
});
