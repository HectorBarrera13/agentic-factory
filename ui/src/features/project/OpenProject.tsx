import { ArrowLeft, Bot, Check, FolderOpen, FolderSearch, History, Search, Sparkles, Wand2 } from "lucide-react";
import * as React from "react";
import {
  Badge,
  Button,
  Callout,
  Dialog,
  DialogContent,
  Field,
  Input,
  ListInput,
  Select,
} from "@/components/ui";
import { api, errorMessage, type Detected, type ProjectConfig } from "@/lib/api";
import { useApp, useLoad } from "@/lib/app-state";
import { ModelPicker } from "@/features/settings/ModelPicker";
import { ProviderSelect } from "@/features/settings/SettingsView";
import { VerifyEditor } from "@/features/settings/VerifyEditor";

export function WelcomeScreen({ onOpen, unconfigured }: { onOpen: (browse?: boolean) => void; unconfigured?: boolean }) {
  const { session } = useApp();
  const project = session?.project;
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="w-full max-w-lg text-center">
        <div className="mx-auto mb-5 flex size-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <Sparkles className="size-7" />
        </div>
        <h1 className="text-xl font-semibold">Factory Studio</h1>
        <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
          Tu código a un lado, tu fábrica al otro: Change Requests, runs con gates humanos y
          evidencia, sobre cualquier proyecto con git.
        </p>
        {unconfigured && project ? (
          <div className="mt-6 text-left">
            <Callout tone="warning" title={`${project.name} aún no tiene .factory/config.toml`}>
              Configúralo con el asistente: detecta lenguaje, carpetas y comandos de verificación,
              y tú revisas antes de guardar.
            </Callout>
          </div>
        ) : null}
        <div className="mt-6 flex justify-center gap-2">
          <Button onClick={() => onOpen()}>
            {unconfigured ? <Wand2 /> : <FolderOpen />}
            {unconfigured ? "Configurar proyecto" : "Abrir proyecto"}
          </Button>
          {unconfigured ? null : (
            <Button variant="outline" onClick={() => onOpen(true)}>
              <FolderSearch /> Examinar…
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

type Step = "path" | "review";

export function OpenProjectDialog({
  open,
  onOpenChange,
  browseFirst = false,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  /** Abre el explorador del sistema en cuanto se muestra el dialogo. */
  browseFirst?: boolean;
}) {
  const { session, refreshSession, notify } = useApp();
  const [step, setStep] = React.useState<Step>("path");
  const [path, setPath] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [detected, setDetected] = React.useState<Detected | null>(null);
  const [config, setConfig] = React.useState<ProjectConfig | null>(null);
  const [browsing, setBrowsing] = React.useState(false);
  const providers = useLoad(() => (step === "review" ? api.providers() : Promise.resolve(null)), [step]);
  const probes = useLoad(() => (step === "review" ? api.probes() : Promise.resolve(null)), [step]);

  React.useEffect(() => {
    if (open) {
      setStep("path");
      setError(null);
      setPath(session?.project?.root ?? "");
    }
  }, [open, session?.project?.root]);

  const analyze = async (target = path) => {
    if (!target.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const d = await api.detect(target.trim());
      setDetected(d);
      if (d.has_config) {
        await api.openProject(target.trim());
        await refreshSession();
        notify("Proyecto abierto", "success");
        onOpenChange(false);
        return;
      }
      setConfig(d.config);
      setStep("review");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  // explorador nativo del sistema (lo abre el servidor local); elegir carpeta = analizarla
  const browse = async () => {
    setBrowsing(true);
    setError(null);
    try {
      const { path: chosen } = await api.browse();
      if (chosen) {
        setPath(chosen);
        await analyze(chosen);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBrowsing(false);
    }
  };

  const browseRequested = React.useRef(false);
  React.useEffect(() => {
    if (open && browseFirst && !browseRequested.current) {
      browseRequested.current = true;
      browse();
    }
    if (!open) browseRequested.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, browseFirst]);

  const save = async () => {
    if (!config) return;
    setBusy(true);
    setError(null);
    try {
      await api.openProject(path.trim());
      await api.saveConfig(config);
      await refreshSession();
      notify("Configuración guardada en .factory/config.toml", "success");
      onOpenChange(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const patch = (fn: (c: ProjectConfig) => void) =>
    setConfig((c) => {
      if (!c) return c;
      const next = structuredClone(c);
      fn(next);
      return next;
    });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={step === "path" ? "Abrir proyecto" : "Revisar configuración detectada"}
        description={
          step === "path"
            ? "Ruta local de un repo git. Si no tiene .factory/, el asistente propone una configuración."
            : "Nada se escribe hasta que guardes. Todo se puede cambiar luego en Ajustes."
        }
        className="w-[min(720px,94vw)]"
      >
        {step === "path" ? (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              analyze();
            }}
          >
            <Field label="Carpeta del proyecto">
              <div className="flex gap-2">
                <Input
                  autoFocus
                  aria-label="Carpeta del proyecto"
                  placeholder="C:\ruta\a\mi-proyecto"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                />
                <Button type="button" variant="outline" loading={browsing} onClick={browse} title="Elegir la carpeta con el explorador del sistema">
                  <FolderSearch /> Examinar…
                </Button>
              </div>
              {browsing ? (
                <p className="mt-1 text-xs text-muted-foreground">Elige la carpeta en la ventana del explorador (puede quedar detrás del navegador).</p>
              ) : null}
            </Field>
            {session?.recent.length ? (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <History className="size-3.5" /> Recientes
                </p>
                <div className="flex flex-col gap-0.5">
                  {session.recent.map((r) => (
                    <button
                      key={r}
                      type="button"
                      onClick={() => {
                        setPath(r);
                        analyze(r);
                      }}
                      className="truncate rounded px-2 py-1.5 text-left font-mono text-xs hover:bg-accent"
                    >
                      {r}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            {error ? <Callout tone="danger" title="No se pudo abrir">{error}</Callout> : null}
            <div className="flex justify-end">
              <Button type="submit" loading={busy} disabled={!path.trim()}>
                <Search /> Abrir
              </Button>
            </div>
          </form>
        ) : config ? (
          <div className="space-y-5">
            <div className="space-y-1.5">
              {detected?.notes.map((n) => (
                <p key={n} className="flex items-start gap-1.5 text-xs text-muted-foreground">
                  <Check className="mt-0.5 size-3.5 shrink-0 text-success" /> {n}
                </p>
              ))}
            </div>
            <section className="grid grid-cols-2 gap-3">
              <Field label="Lenguaje" hint="La guardia de integridad de tests solo existe para Python.">
                <Select
                  value={config.code.language}
                  onChange={(e) => patch((c) => (c.code.language = e.target.value))}
                >
                  {["python", "javascript", "typescript", "other"].map((l) => (
                    <option key={l}>{l}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Rama base">
                <Input
                  value={config.git.base_branch}
                  onChange={(e) => patch((c) => (c.git.base_branch = e.target.value))}
                />
              </Field>
              <Field label="Código fuente (escribible, separado por comas)">
                <ListInput value={config.code.source} onChange={(v) => patch((c) => (c.code.source = v))} />
              </Field>
              <Field label="Carpeta de tests (escribible por Test Analyst)">
                <Input
                  value={config.code.tests}
                  onChange={(e) => patch((c) => (c.code.tests = e.target.value))}
                />
              </Field>
              <Field label="Requisitos (opcional)">
                <Input
                  value={config.docs.requirements ?? ""}
                  onChange={(e) => patch((c) => (c.docs.requirements = e.target.value || null))}
                />
              </Field>
              <Field label="Spec (opcional)">
                <Input
                  value={config.docs.spec ?? ""}
                  onChange={(e) => patch((c) => (c.docs.spec = e.target.value || null))}
                />
              </Field>
            </section>
            <section>
              <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
                <Bot className="size-3.5" /> Modelo de los agentes
              </h4>
              <div className="grid grid-cols-[12rem_1fr] gap-3">
                <Field label="Proveedor">
                  <ProviderSelect
                    label="Proveedor del proyecto"
                    value={config.agents.provider}
                    providers={providers.data?.items ?? []}
                    onChange={(v) =>
                      patch((c) => {
                        if (!v || v === c.agents.provider) return;
                        c.agents.provider = v;
                        c.agents.model = "";
                      })
                    }
                  />
                </Field>
                <Field
                  label="Modelo"
                  hint={
                    detected?.suggested_model &&
                    detected.suggested_model.provider === config.agents.provider &&
                    detected.suggested_model.model === config.agents.model
                      ? "Sugerido: el último modelo que pasó la prueba."
                      : undefined
                  }
                >
                  <ModelPicker
                    label="Modelo del proyecto"
                    provider={config.agents.provider}
                    value={config.agents.model}
                    onChange={(v) => patch((c) => (c.agents.model = v))}
                    probes={probes.data}
                  />
                </Field>
              </div>
            </section>
            <section>
              <div className="mb-2 flex items-center justify-between">
                <h4 className="text-xs font-semibold">Comandos de verificación</h4>
                <Badge>se ejecutan en el worktree del run, sin shell</Badge>
              </div>
              <VerifyEditor
                commands={config.verify.commands}
                onChange={(cmds) => patch((c) => (c.verify.commands = cmds))}
              />
            </section>
            {error ? <Callout tone="danger" title="Configuración inválida">{error}</Callout> : null}
            <div className="flex justify-between">
              <Button variant="ghost" onClick={() => setStep("path")}>
                <ArrowLeft /> Atrás
              </Button>
              <Button onClick={save} loading={busy}>
                <Check /> Guardar y abrir
              </Button>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
