import { AlertTriangle, CheckCircle2, CircleDashed, FilePlus2, FileText, Hand, Loader2, Lock, Pause, Pencil, Play, Rocket, Save, X } from "lucide-react";
import * as React from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Badge,
  Button,
  Callout,
  Dialog,
  DialogContent,
  EmptyState,
  Field,
  Input,
  Textarea,
} from "@/components/ui";
import { api, errorMessage, type RunSummary } from "@/lib/api";
import { useApp, useLoad } from "@/lib/app-state";
import { statusLabel, statusTone } from "@/lib/phases";
import { cn } from "@/lib/utils";
import { RunBoard } from "./RunBoard";

const CR_TEMPLATE = `## Problema

## Alcance

## Criterios de aceptación
- AC-...

## Fuera de alcance
`;

export function FactoryView() {
  const { tick, selectedRun, setSelectedRun } = useApp();
  const crs = useLoad(() => api.crs(), [tick]);
  const runs = useLoad(() => api.runs(), [tick]);
  const [creating, setCreating] = React.useState(false);

  const runById = React.useMemo(() => {
    const m: Record<string, RunSummary> = {};
    for (const r of runs.data?.items ?? []) m[r.change_id] = r;
    return m;
  }, [runs.data]);

  const items = crs.data?.items ?? [];
  const selected = selectedRun && items.some((c) => c.change_id === selectedRun) ? selectedRun : null;

  return (
    <div className="flex h-full">
      <aside className="flex w-72 shrink-0 flex-col border-r border-border bg-sidebar">
        <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Change Requests</span>
          <Button size="xs" onClick={() => setCreating(true)}>
            <FilePlus2 /> Nuevo
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-1.5">
          {crs.error ? <Callout tone="danger">{crs.error}</Callout> : null}
          {!crs.loading && !items.length ? (
            <EmptyState icon={<FileText />} title="Sin Change Requests" description="Un CR describe un cambio acotado con criterios de aceptación." action={<Button size="sm" onClick={() => setCreating(true)}>Crear el primero</Button>} />
          ) : null}
          {items.map((cr) => {
            const r = runById[cr.change_id];
            return (
              <button
                key={cr.change_id}
                type="button"
                onClick={() => setSelectedRun(cr.change_id)}
                className={cn(
                  "mb-0.5 flex w-full flex-col gap-1 rounded-md px-2.5 py-2 text-left hover:bg-accent",
                  selected === cr.change_id && "bg-accent ring-1 ring-primary/40",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs font-semibold">{cr.change_id}</span>
                  {r ? (
                    <Badge tone={statusTone(r.status, r.awaiting_gate)} className="max-w-36 truncate" data-run-status={r.busy ? "running" : r.status}>
                      <RunStatusIcon run={r} />
                      {r.busy ? "en curso" : statusLabel(r.status, r.awaiting_gate)}
                    </Badge>
                  ) : (
                    <Badge>sin run</Badge>
                  )}
                </div>
                <span className="line-clamp-2 text-xs text-muted-foreground">{cr.title}</span>
              </button>
            );
          })}
        </div>
      </aside>
      <section className="min-w-0 flex-1">
        {selected ? (
          runById[selected] ? (
            <RunBoard key={selected} changeId={selected} />
          ) : (
            <CrDetail key={selected} changeId={selected} onStarted={() => runs.reload()} />
          )
        ) : (
          <EmptyState
            className="h-full"
            icon={<Rocket />}
            title="Elige un Change Request"
            description="Cada run avanza por fases fijas y se detiene en cuatro gates humanos: requisitos, plan, review y release. Nada se aprueba solo."
          />
        )}
      </section>
      <CreateCrDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(id) => {
          crs.reload();
          setSelectedRun(id);
        }}
      />
    </div>
  );
}

function CrDetail({ changeId, onStarted }: { changeId: string; onStarted: () => void }) {
  const { notify } = useApp();
  const cr = useLoad(() => api.cr(changeId), [changeId]);
  const [editing, setEditing] = React.useState(false);
  const [body, setBody] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (cr.data) setBody(cr.data.body);
  }, [cr.data]);

  if (cr.error) return <div className="p-6"><Callout tone="danger">{cr.error}</Callout></div>;
  if (!cr.data) return <div className="p-6 text-sm text-muted-foreground">Cargando…</div>;

  const save = async () => {
    setBusy(true);
    try {
      await api.updateCr(changeId, body);
      notify("Change Request guardado", "success");
      setEditing(false);
      cr.reload();
    } catch (err) {
      notify(errorMessage(err), "danger");
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    setBusy(true);
    try {
      await api.startRun(changeId);
      notify(`Run ${changeId} creado. Pulsa Avanzar para ejecutar el intake.`, "success");
      onStarted();
    } catch (err) {
      notify(errorMessage(err), "danger");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-border px-5 py-3">
        <div className="min-w-0">
          <h2 className="font-mono text-base font-semibold">{changeId}</h2>
          <p className="text-xs text-muted-foreground">{cr.data.file}</p>
        </div>
        <div className="flex-1" />
        {cr.data.frozen ? (
          <Badge><Lock className="size-3" /> congelado: ya tiene run</Badge>
        ) : editing ? (
          <>
            <Button variant="ghost" size="sm" onClick={() => { setEditing(false); setBody(cr.data!.body); }}>
              <X /> Cancelar
            </Button>
            <Button size="sm" onClick={save} loading={busy}>
              <Save /> Guardar
            </Button>
          </>
        ) : (
          <>
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Pencil /> Editar
            </Button>
            <Button size="sm" onClick={start} loading={busy}>
              <Play /> Iniciar run
            </Button>
          </>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        {editing ? (
          <div className="grid h-full grid-cols-2">
            <Textarea aria-label="Contenido del CR" className="h-full resize-none rounded-none border-0 border-r p-5 text-xs focus-visible:ring-0" value={body} onChange={(e) => setBody(e.target.value)} />
            <div className="prose-factory overflow-auto p-5">
              <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
            </div>
          </div>
        ) : (
          <div className="prose-factory mx-auto max-w-3xl p-6">
            <Markdown remarkPlugins={[remarkGfm]}>{cr.data.body}</Markdown>
          </div>
        )}
      </div>
    </div>
  );
}

function CreateCrDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: (id: string) => void;
}) {
  const { notify } = useApp();
  const [id, setId] = React.useState("CR-");
  const [title, setTitle] = React.useState("");
  const [body, setBody] = React.useState(CR_TEMPLATE);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setId("CR-");
      setTitle("");
      setBody(CR_TEMPLATE);
      setError(null);
    }
  }, [open]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const cid = id.trim().toUpperCase();
      const r = await api.createCr(cid, title.trim(), body);
      notify(`Creado ${r.file}`, "success");
      onOpenChange(false);
      onCreated(cid);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Nuevo Change Request" description="Se guarda como markdown en la carpeta de CRs del proyecto." className="w-[min(760px,94vw)]">
        <form className="space-y-3" onSubmit={submit}>
          <div className="grid grid-cols-[10rem_1fr] gap-3">
            <Field label="ID" hint="CR-<letras/números>">
              <Input autoFocus value={id} onChange={(e) => setId(e.target.value)} className="font-mono" />
            </Field>
            <Field label="Título">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Qué cambia, en una línea" />
            </Field>
          </div>
          <Field label="Descripción (markdown)">
            <Textarea className="min-h-64 text-xs" value={body} onChange={(e) => setBody(e.target.value)} />
          </Field>
          {error ? <Callout tone="danger">{error}</Callout> : null}
          <div className="flex justify-end">
            <Button type="submit" loading={busy} disabled={!/^CR-[A-Za-z0-9]/.test(id.trim()) || !title.trim()}>
              <FilePlus2 /> Crear
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Icono del estado en la lista de CRs: que se lea de un vistazo sin leer la etiqueta. */
function RunStatusIcon({ run }: { run: RunSummary }) {
  const cls = "size-3 shrink-0";
  if (run.busy) return <Loader2 aria-hidden className={cn(cls, "animate-spin")} />;
  if (run.status === "READY FOR HUMAN MERGE") return <CheckCircle2 aria-hidden className={cls} />;
  if (run.status === "ESCALATED" || run.status === "NOT READY") return <AlertTriangle aria-hidden className={cls} />;
  if (run.awaiting_gate) return <Hand aria-hidden className={cls} />;
  if (run.paused) return <Pause aria-hidden className={cls} />;
  return <CircleDashed aria-hidden className={cls} />;
}
