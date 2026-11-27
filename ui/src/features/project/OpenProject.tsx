import { ArrowLeft, Check, FolderOpen, History, Search, Sparkles, Wand2 } from "lucide-react";
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
import { useApp } from "@/lib/app-state";
import { VerifyEditor } from "@/features/settings/VerifyEditor";

export function WelcomeScreen({ onOpen, unconfigured }: { onOpen: () => void; unconfigured?: boolean }) {
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
        <Button className="mt-6" onClick={onOpen}>
          {unconfigured ? <Wand2 /> : <FolderOpen />}
          {unconfigured ? "Configurar proyecto" : "Abrir proyecto"}
        </Button>
      </div>
    </div>
  );
}

type Step = "path" | "review";

export function OpenProjectDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const { session, refreshSession, notify } = useApp();
  const [step, setStep] = React.useState<Step>("path");
  const [path, setPath] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [detected, setDetected] = React.useState<Detected | null>(null);
  const [config, setConfig] = React.useState<ProjectConfig | null>(null);

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
              <Input
                autoFocus
                placeholder="C:\ruta\a\mi-proyecto"
                value={path}
                onChange={(e) => setPath(e.target.value)}
              />
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
