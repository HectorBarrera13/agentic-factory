import { CheckCircle2, KeyRound, Plug, RefreshCw, Save, Trash2, XCircle } from "lucide-react";
import * as React from "react";
import { Badge, Button, Callout, Card, CardHeader, Field, Input } from "@/components/ui";
import {
  api,
  errorMessage,
  type ConnectionTest,
  type ProviderId,
  type ProviderInfo,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const KIND_TEXT: Record<string, string> = {
  INVALID_KEY: "Clave inválida o sin permiso",
  UNREACHABLE: "Sin conexión con el servidor",
  NO_KEY: "Falta la API key",
  NO_URL: "Falta la URL del servidor",
  ERROR: "Error del proveedor",
};

/** El proveedor tiene lo minimo para usarse (clave o URL segun el caso). */
export function isConfigured(p: ProviderInfo): boolean {
  return p.needs_key ? p.status.has_key : Boolean(p.status.url);
}

export function TestResult({ test }: { test: ConnectionTest }) {
  return test.ok ? (
    <p className="flex items-center gap-1.5 text-sm text-success">
      <CheckCircle2 className="size-4" /> Conectado · {test.models} modelos
    </p>
  ) : (
    <p className="flex items-start gap-1.5 text-sm text-danger">
      <XCircle className="mt-0.5 size-4 shrink-0" />
      <span>
        {KIND_TEXT[test.kind] ?? test.kind}
        {test.message && test.kind !== "NO_KEY" && test.kind !== "NO_URL" ? (
          <span className="block text-xs text-muted-foreground">{test.message}</span>
        ) : null}
      </span>
    </p>
  );
}

const SOURCE_TEXT = { env: "desde variable de entorno", file: "guardada en tu equipo", none: "" };

export function ProvidersPanel({
  providers,
  onChanged,
}: {
  providers: ProviderInfo[];
  onChanged: () => void;
}) {
  const [selected, setSelected] = React.useState<ProviderId>(providers[0]?.id ?? "gemini");
  const [tests, setTests] = React.useState<Partial<Record<ProviderId, ConnectionTest>>>({});
  const current = providers.find((p) => p.id === selected) ?? providers[0];
  if (!current) return null;

  return (
    <Card>
      <CardHeader
        title="Proveedores de modelos"
        description="Elige un proveedor y configura su acceso. Las claves se guardan solo en tu equipo (~/.agentic-factory/credentials.json), nunca en el proyecto, y la UI nunca las vuelve a mostrar."
      />
      <div className="grid grid-cols-[13rem_1fr] divide-x divide-border">
        <nav aria-label="Proveedores" className="flex flex-col p-2">
          {providers.map((p) => {
            const test = tests[p.id];
            // verde = probado OK, o con clave/URL propia; una URL por defecto sin probar no cuenta
            const ok = test ? test.ok : p.needs_key ? p.status.has_key : p.status.url_source !== "none";
            return (
              <button
                key={p.id}
                type="button"
                aria-pressed={p.id === current.id}
                onClick={() => setSelected(p.id)}
                className={cn(
                  "flex items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                  p.id === current.id ? "bg-accent font-medium" : "hover:bg-accent/60",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "size-2 shrink-0 rounded-full",
                    test && !test.ok ? "bg-danger" : ok ? "bg-success" : "bg-muted-foreground/40",
                  )}
                />
                <span className="min-w-0 flex-1 truncate">{p.label}</span>
              </button>
            );
          })}
        </nav>
        <ProviderForm
          key={current.id}
          provider={current}
          test={tests[current.id]}
          onTest={(t) => setTests((all) => ({ ...all, [current.id]: t }))}
          onChanged={onChanged}
        />
      </div>
    </Card>
  );
}

function ProviderForm({
  provider: p,
  test,
  onTest,
  onChanged,
}: {
  provider: ProviderInfo;
  test?: ConnectionTest;
  onTest: (t: ConnectionTest) => void;
  onChanged: () => void;
}) {
  const [key, setKey] = React.useState("");
  const [url, setUrl] = React.useState(p.status.url_source === "file" ? p.status.url ?? "" : "");
  const [busy, setBusy] = React.useState<"save" | "test" | "delete" | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const usesKey = p.needs_key || p.key_optional;
  const keyFromEnv = p.status.key_source === "env";
  const urlFromEnv = p.status.url_source === "env";
  const urlChanged = p.needs_url && url.trim() !== "" && url.trim() !== (p.status.url ?? "");
  const canSave = (usesKey && !keyFromEnv && key.trim() !== "") || (urlChanged && !urlFromEnv);

  const run = async (kind: "save" | "test" | "delete") => {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "save") {
        const body: { api_key?: string; url?: string } = {};
        if (key.trim()) body.api_key = key.trim();
        if (urlChanged) body.url = url.trim();
        const res = await api.saveProvider(p.id, body);
        setKey("");
        onTest(res.test);
      } else if (kind === "test") {
        onTest(await api.testProvider(p.id));
      } else {
        await api.deleteProviderKey(p.id);
        onTest({ ok: false, kind: "NO_KEY", message: "", models: 0 });
      }
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <form
      className="space-y-4 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSave) run("save");
      }}
    >
      <div>
        <h3 className="text-sm font-semibold">{p.label}</h3>
        <p className="text-xs text-muted-foreground">{p.description}</p>
      </div>

      {usesKey ? (
        <Field
          label={p.key_optional ? "API key (opcional)" : "API key"}
          hint={keyFromEnv ? "Viene de una variable de entorno: cámbiala ahí." : undefined}
        >
          <div className="flex items-center gap-2">
            <Input
              type="password"
              autoComplete="off"
              spellCheck={false}
              aria-label={`API key de ${p.label}`}
              disabled={keyFromEnv}
              placeholder={p.status.has_key ? `•••• ${p.status.last4 ?? ""}` : "Pega tu API key"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
            {p.status.has_key ? (
              <Badge tone={keyFromEnv ? "info" : "neutral"}>
                <KeyRound className="size-3" /> {SOURCE_TEXT[p.status.key_source]}
              </Badge>
            ) : null}
          </div>
        </Field>
      ) : null}

      {p.needs_url ? (
        <Field
          label="URL del servidor"
          hint={urlFromEnv ? "Viene de una variable de entorno: cámbiala ahí." : p.default_url ? `Por defecto ${p.default_url}` : "Ej.: http://localhost:1234"}
        >
          <Input
            aria-label={`URL de ${p.label}`}
            disabled={urlFromEnv}
            placeholder={p.status.url ?? p.default_url ?? "http://localhost:1234"}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </Field>
      ) : null}

      {test ? <TestResult test={test} /> : null}
      {error ? <Callout tone="danger">{error}</Callout> : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="sm" disabled={!canSave} loading={busy === "save"}>
          <Save /> Guardar y probar
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy !== null}
          loading={busy === "test"}
          onClick={() => run("test")}
        >
          {test ? <RefreshCw /> : <Plug />} Probar conexión
        </Button>
        {p.status.key_source === "file" ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="ml-auto text-danger"
            loading={busy === "delete"}
            onClick={() => run("delete")}
          >
            <Trash2 /> Borrar clave
          </Button>
        ) : null}
      </div>
    </form>
  );
}
