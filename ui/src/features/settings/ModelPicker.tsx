import { FlaskConical, Loader2, RefreshCw } from "lucide-react";
import * as React from "react";
import { Badge, Button, Input } from "@/components/ui";
import {
  api,
  errorMessage,
  probeKey,
  type ModelEntry,
  type ProbeResult,
  type Probes,
  type ProviderId,
} from "@/lib/api";

interface ModelList {
  items: ModelEntry[];
  error: string | null;
}

// Una carga por proveedor y sesion de la pagina (el backend tambien cachea 10 min).
const cache = new Map<ProviderId, Promise<ModelList>>();

function fetchModels(provider: ProviderId, refresh = false): Promise<ModelList> {
  if (refresh || !cache.has(provider)) {
    const p = api
      .providerModels(provider, refresh)
      .then((r) => ({ items: r.items, error: r.error ? r.error.message : null }))
      .catch((err) => ({ items: [], error: errorMessage(err) }));
    cache.set(provider, p);
  }
  return cache.get(provider)!;
}

export function resetModelCache() {
  cache.clear();
}

export function useModels(provider: ProviderId, version = 0) {
  const [state, setState] = React.useState<ModelList & { loading: boolean }>({ items: [], error: null, loading: true });
  const load = React.useCallback(
    (refresh: boolean) => {
      setState((s) => ({ ...s, loading: true }));
      let alive = true;
      fetchModels(provider, refresh).then((r) => alive && setState({ ...r, loading: false }));
      return () => {
        alive = false;
      };
    },
    [provider],
  );
  React.useEffect(() => load(false), [load, version]);
  return { ...state, refresh: () => load(true) };
}

const PROBE_TEXT: Record<ProbeResult["status"], { tone: "success" | "warning" | "danger"; label: string }> = {
  OK: { tone: "success", label: "disponible" },
  NO_TOOLS: { tone: "warning", label: "no usa tools" },
  NO_ACCESS: { tone: "danger", label: "sin acceso en tu plan" },
  QUOTA: { tone: "warning", label: "sin cuota" },
  INVALID_KEY: { tone: "danger", label: "clave inválida" },
  ERROR: { tone: "danger", label: "error" },
};

export function ProbeChip({ result, running }: { result?: ProbeResult; running: boolean }) {
  if (running)
    return (
      <Badge tone="info" className="shrink-0">
        <Loader2 className="size-3 animate-spin" /> probando…
      </Badge>
    );
  if (!result) return <Badge className="shrink-0">sin probar</Badge>;
  const t = PROBE_TEXT[result.status] ?? PROBE_TEXT.ERROR;
  return (
    <Badge tone={t.tone} title={result.message} className="shrink-0">
      {t.label}
    </Badge>
  );
}

export function ModelPicker({
  provider,
  value,
  onChange,
  placeholder,
  probes,
  label,
  providersVersion = 0,
}: {
  provider: ProviderId;
  /** "" = hereda (cuando hay placeholder) */
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  probes: Probes | null;
  label: string;
  /** cambia cuando se guarda una clave/URL: recarga la lista */
  providersVersion?: number;
}) {
  const models = useModels(provider, providersVersion);
  const [probing, setProbing] = React.useState(false);
  const [probeError, setProbeError] = React.useState<string | null>(null);
  const listId = React.useId();
  const effective = value || placeholder || "";
  const key = probeKey(provider, effective);
  const result = probes?.items[key];
  const running = probing || Boolean(probes?.running.includes(key));
  const known = models.items.some((m) => m.id === effective);

  const probe = async () => {
    setProbing(true);
    setProbeError(null);
    try {
      await api.probe(provider, effective);
    } catch (err) {
      setProbeError(errorMessage(err));
    } finally {
      setProbing(false);
    }
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <Input
          aria-label={label}
          className="min-w-0 flex-1"
          list={listId}
          spellCheck={false}
          placeholder={placeholder ?? (models.loading ? "cargando modelos…" : "elige o escribe un modelo")}
          value={value}
          onChange={(e) => onChange(e.target.value.trim())}
        />
        <datalist id={listId}>
          {models.items.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label !== m.id ? m.label : undefined}
            </option>
          ))}
        </datalist>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={`Recargar modelos de ${label}`}
          title="Recargar la lista del proveedor"
          onClick={models.refresh}
        >
          <RefreshCw className={models.loading ? "animate-spin" : undefined} />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={`Probar ${label}`}
          className="shrink-0"
          disabled={!effective || running}
          onClick={probe}
        >
          <FlaskConical /> Probar
        </Button>
        {effective ? <ProbeChip result={result} running={running} /> : null}
      </div>
      {!value && placeholder ? null : models.error ? (
        <p className="text-[11px] text-warning">Sin lista del proveedor ({models.error}); puedes escribir el nombre a mano.</p>
      ) : !models.loading && effective && models.items.length > 0 && !known ? (
        <p className="text-[11px] text-warning">«{effective}» no aparece en la lista del proveedor: verifica el nombre o pruébalo.</p>
      ) : null}
      {result && !running && result.status !== "OK" ? (
        <p className="text-[11px] text-muted-foreground">{result.message}</p>
      ) : null}
      {probeError ? <p className="text-[11px] text-danger">{probeError}</p> : null}
    </div>
  );
}
