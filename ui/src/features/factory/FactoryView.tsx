import { Archive as ArchiveIcon, ArchiveRestore, FilePlus2, FileText, Lock, Pencil, Play, Rocket, Save, Trash2, X } from "lucide-react";
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
  LoadingBlock,
  Menu,
  Textarea,
} from "@/components/ui";
import { api, errorMessage, type ChangeRequest, type RunSummary } from "@/lib/api";
import { useApp, useLoad } from "@/lib/app-state";
import { statusLabel, statusShort, statusTone } from "@/lib/phases";
import { cn } from "@/lib/utils";
import { CrEditor, DeleteCrDialog, useSaveCr } from "./CrEditor";
import { RunBoard } from "./RunBoard";
import { StatusIcon } from "./StatusIcon";

const CR_TEMPLATE = `## Problema

## Alcance

## Criterios de aceptación
- AC-...

## Fuera de alcance
`;

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("es", { dateStyle: "short", timeStyle: "short" });
}

type Filter = "active" | "archived";

export function FactoryView() {
  const { tick, selectedRun, setSelectedRun, notify } = useApp();
  const [filter, setFilter] = React.useState<Filter>("active");
  const crs = useLoad(() => api.crs(), [tick]);
  const archived = useLoad(() => api.crs(true), [tick, filter]);
  const runs = useLoad(() => api.runs(), [tick]);
  const [creating, setCreating] = React.useState(false);
  const [deleting, setDeleting] = React.useState<string | null>(null);

  const runById = React.useMemo(() => {
    const m: Record<string, RunSummary> = {};
    for (const r of runs.data?.items ?? []) m[r.change_id] = r;
    return m;
  }, [runs.data]);

  const { reload: reloadCrs } = crs;
  const { reload: reloadRuns } = runs;
  const { reload: reloadArchived } = archived;
  const reloadAll = React.useCallback(() => {
    reloadCrs();
    reloadRuns();
    reloadArchived();
  }, [reloadCrs, reloadRuns, reloadArchived]);

  const items = crs.data?.items ?? [];
  const archivedItems = archived.data?.items ?? [];
  const selected = selectedRun && items.some((c) => c.change_id === selectedRun) ? selectedRun : null;

  const restore = async (id: string) => {
    try {
      await api.restoreCr(id);
      notify(`${id} restaurado`, "success");
      reloadAll();
      setFilter("active");
      setSelectedRun(id);
    } catch (err) {
      notify(errorMessage(err), "danger");
    }
  };

  const list = filter === "active" ? items : archivedItems;
  const current = filter === "active" ? crs : archived;
  const loading = current.loading && !current.data;

  const tabs: [Filter, string, number][] = [
    ["active", "Activos", items.length],
    ["archived", "Archivados", archivedItems.length],
  ];

  return (
    <div className="flex h-full">
      <aside className="flex w-72 shrink-0 flex-col border-r border-border bg-sidebar">
        <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2.5">
          <span className="truncate text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Change Requests</span>
          <Button size="xs" onClick={() => setCreating(true)}>
            <FilePlus2 /> Nuevo
          </Button>
        </div>
        <div role="tablist" aria-label="Filtro de Change Requests" className="flex gap-1 border-b border-border p-1.5">
          {tabs.map(([id, label, count]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={filter === id}
              onClick={() => setFilter(id)}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors duration-150 hover:bg-accent hover:text-foreground",
                filter === id && "bg-primary/12 text-primary hover:bg-primary/15 hover:text-primary",
              )}
            >
              {label}
              <span className="rounded-full bg-muted px-1.5 text-[10px] tabular-nums text-muted-foreground">{count}</span>
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-1.5">
          {current.error ? <Callout tone="danger">{current.error}</Callout> : null}
          {loading ? <LoadingBlock label="Cargando Change Requests…" lines={4} /> : null}
          {!loading && !list.length ? (
            filter === "active" ? (
              <EmptyState
                icon={<FileText />}
                title="Sin Change Requests"
                description="Un CR describe un cambio acotado con criterios de aceptación."
                action={<Button size="sm" onClick={() => setCreating(true)}>Crear el primero</Button>}
              />
            ) : (
              <EmptyState icon={<ArchiveIcon />} title="Nada archivado" description="Los runs terminados se archivan desde su cabecera." />
            )
          ) : null}
          <div className="anim-list" key={filter}>
            {filter === "active"
              ? items.map((cr, i) => (
                  <CrListItem
                    key={cr.change_id}
                    cr={cr}
                    run={runById[cr.change_id]}
                    index={i}
                    selected={selected === cr.change_id}
                    onSelect={() => setSelectedRun(cr.change_id)}
                  />
                ))
              : archivedItems.map((cr, i) => (
                  <ArchivedItem
                    key={cr.change_id}
                    cr={cr}
                    index={i}
                    onRestore={() => restore(cr.change_id)}
                    onDelete={() => setDeleting(cr.change_id)}
                  />
                ))}
          </div>
        </div>
      </aside>
      <section className="min-w-0 flex-1">
        {filter === "archived" ? (
          <EmptyState
            className="h-full"
            icon={<ArchiveIcon />}
            title="Change Requests archivados"
            description="Archivar oculta el CR y su run y quita el worktree; la rama sigue. Restaura uno para volver a trabajar con él o elimínalo para siempre."
          />
        ) : selected ? (
          runById[selected] ? (
            <RunBoard key={selected} changeId={selected} onRemoved={reloadAll} onRestore={restore} />
          ) : (
            <CrDetail
              key={selected}
              changeId={selected}
              onStarted={() => reloadRuns()}
              onDeleted={() => {
                setSelectedRun(null);
                reloadAll();
              }}
            />
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
          reloadCrs();
          setFilter("active");
          setSelectedRun(id);
        }}
      />
      {deleting ? (
        <DeleteCrDialog
          changeId={deleting}
          hasRun
          open
          onOpenChange={(v) => {
            if (!v) setDeleting(null);
          }}
          onDeleted={() => {
            setDeleting(null);
            reloadAll();
          }}
        />
      ) : null}
    </div>
  );
}

function CrListItem({
  cr,
  run,
  index,
  selected,
  onSelect,
}: {
  cr: ChangeRequest;
  run?: RunSummary;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      style={{ ["--i" as string]: index }}
      className={cn(
        "mb-0.5 flex w-full min-w-0 flex-col gap-1 rounded-md px-2.5 py-2 text-left transition-colors duration-150 hover:bg-accent",
        selected && "bg-accent ring-1 ring-primary/40",
      )}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <span className="shrink-0 font-mono text-xs font-semibold">{cr.change_id}</span>
        {run ? (
          <Badge
            tone={statusTone(run.status, run.awaiting_gate)}
            title={run.busy ? "En curso" : statusLabel(run.status, run.awaiting_gate)}
            data-run-status={run.busy ? "running" : run.status}
          >
            <StatusIcon status={run.status} awaiting={run.awaiting_gate} busy={run.busy} paused={run.paused} />
            {run.busy ? "en curso" : statusShort(run.status, run.awaiting_gate)}
          </Badge>
        ) : (
          <Badge>sin run</Badge>
        )}
      </div>
      <span className="line-clamp-2 break-words text-xs text-muted-foreground" title={cr.title}>
        {cr.title}
      </span>
    </button>
  );
}

function ArchivedItem({
  cr,
  index,
  onRestore,
  onDelete,
}: {
  cr: ChangeRequest;
  index: number;
  onRestore: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      style={{ ["--i" as string]: index }}
      data-archived="true"
      className="mb-0.5 flex min-w-0 items-start gap-1 rounded-md px-2.5 py-2 opacity-75 transition-[opacity,background-color] duration-150 hover:bg-accent hover:opacity-100 focus-within:opacity-100"
    >
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 font-mono text-xs font-semibold">{cr.change_id}</span>
          {cr.status ? (
            <Badge tone={statusTone(cr.status)} title={statusLabel(cr.status)}>
              <StatusIcon status={cr.status} />
              {statusShort(cr.status)}
            </Badge>
          ) : null}
        </div>
        <p className="mt-0.5 line-clamp-2 break-words text-xs text-muted-foreground" title={cr.title}>
          {cr.title}
        </p>
        {cr.archived_at ? (
          <p className="mt-0.5 truncate text-[10px] text-muted-foreground" title={cr.archived_by ?? undefined}>
            archivado {formatDate(cr.archived_at)}
            {cr.archived_by ? ` · ${cr.archived_by}` : ""}
          </p>
        ) : null}
      </div>
      <Button size="icon" variant="ghost" onClick={onRestore} aria-label={`Restaurar ${cr.change_id}`} title={`Restaurar ${cr.change_id}`}>
        <ArchiveRestore />
      </Button>
      <Menu
        label={`Más acciones de ${cr.change_id}`}
        items={[
          { label: "Restaurar", icon: <ArchiveRestore />, onSelect: onRestore },
          { label: "Eliminar…", icon: <Trash2 />, danger: true, onSelect: onDelete },
        ]}
      />
    </div>
  );
}

function CrDetail({ changeId, onStarted, onDeleted }: { changeId: string; onStarted: () => void; onDeleted: () => void }) {
  const { notify } = useApp();
  const cr = useLoad(() => api.cr(changeId), [changeId]);
  const [editing, setEditing] = React.useState(false);
  const [body, setBody] = React.useState("");
  const [starting, setStarting] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const { save, busy: saving } = useSaveCr(changeId, () => {
    setEditing(false);
    cr.reload();
  });

  React.useEffect(() => {
    if (cr.data) setBody(cr.data.body);
  }, [cr.data]);

  if (cr.error) return <div className="p-6"><Callout tone="danger">{cr.error}</Callout></div>;
  if (!cr.data) return <LoadingBlock label="Cargando Change Request…" lines={6} />;

  const start = async () => {
    setStarting(true);
    try {
      await api.startRun(changeId);
      notify(`Run ${changeId} creado. Pulsa Avanzar para ejecutar el intake.`, "success");
      onStarted();
    } catch (err) {
      notify(errorMessage(err), "danger");
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="anim-enter flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-5 py-3">
        <div className="min-w-0 flex-1 basis-48">
          <h2 className="font-mono text-base font-semibold">{changeId}</h2>
          <p className="truncate text-xs text-muted-foreground" title={cr.data.file}>
            {cr.data.file}
          </p>
        </div>
        {!cr.data.editable ? (
          <Badge title={cr.data.reason}>
            <Lock className="size-3" /> congelado
          </Badge>
        ) : editing ? (
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setEditing(false);
                setBody(cr.data!.body);
              }}
            >
              <X /> Cancelar
            </Button>
            <Button size="sm" onClick={() => save(body)} loading={saving} disabled={!body.trim()}>
              <Save /> Guardar
            </Button>
          </div>
        ) : (
          <div className="flex shrink-0 items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Pencil /> Editar
            </Button>
            <Button size="sm" onClick={start} loading={starting}>
              <Play /> Iniciar run
            </Button>
            {cr.data.deletable ? (
              <Menu
                label={`Más acciones de ${changeId}`}
                items={[{ label: "Eliminar…", icon: <Trash2 />, danger: true, onSelect: () => setDeleting(true) }]}
              />
            ) : null}
          </div>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        {editing ? (
          <CrEditor value={body} onChange={setBody} />
        ) : (
          <div className="prose-factory mx-auto max-w-3xl break-words p-6">
            <Markdown remarkPlugins={[remarkGfm]}>{cr.data.body}</Markdown>
          </div>
        )}
      </div>
      <DeleteCrDialog changeId={changeId} hasRun={false} open={deleting} onOpenChange={setDeleting} onDeleted={onDeleted} />
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

