import { DiffEditor } from "@monaco-editor/react";
import {
  AlertTriangle,
  Archive,
  Clock,
  FileCode2,
  FileText,
  FolderGit2,
  GitCompare,
  FlaskConical,
  Activity,
  FolderArchive as ArchiveIcon,
  Pause,
  Play,
  RotateCcw,
  Settings2,
  Trash2,
} from "lucide-react";
import * as React from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  LoadingBlock,
  Menu,
  Select,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui";
import { api, errorMessage, roleLabel, type ModelInUse, type RunDetail } from "@/lib/api";
import { useApp, useLoad } from "@/lib/app-state";
import { deriveFlow, useActivity } from "@/lib/activity";
import { statusLabel, statusTone, TERMINAL } from "@/lib/phases";
import { cn, formatTime, languageFor } from "@/lib/utils";
import { ProviderLogo } from "@/components/ProviderLogo";
import { DeleteCrDialog, EditCrButton, useSignature } from "./CrEditor";
import { StatusIcon } from "./StatusIcon";
import { FindingsTable } from "./FindingsTable";
import { GateCard } from "./GateCard";
import { ActivityLog } from "./ActivityLog";
import { FlowDiagram } from "./FlowDiagram";
import { NowStrip } from "./NowStrip";
import { RetryDialog } from "./RetryDialog";

/** Terminados que admiten Reintentar (nuevo intento del CR editado). */
const RETRYABLE = ["ESCALATED", "NOT READY"];

const OUTCOME_COPY: Record<string, { tone: "info" | "warning" | "danger" | "success"; title: string }> = {
  awaiting: { tone: "warning", title: "Detenido en un gate humano" },
  blocked: { tone: "danger", title: "Bloqueado" },
  transient: { tone: "warning", title: "Error transitorio del modelo: reintenta más tarde" },
  error: { tone: "danger", title: "Error al avanzar (el run quedó en su último checkpoint)" },
  done: { tone: "success", title: "Run terminado" },
  paused: { tone: "info", title: "Run pausado" },
};

type Mode = "fake" | "adk";
const LS_MODE = "factory.mode";

/** Modo recordado; por defecto el modelo real (la simulacion es para probar el flujo). */
function readMode(): Mode {
  try {
    return localStorage.getItem(LS_MODE) === "fake" ? "fake" : "adk";
  } catch {
    return "adk";
  }
}

const PROBE_WORDS: Record<string, string> = {
  NO_TOOLS: "no usa tools",
  NO_ACCESS: "sin acceso en tu plan",
  QUOTA: "sin cuota",
  INVALID_KEY: "clave inválida",
  ERROR: "error",
};

const probeWords = (m: ModelInUse) => (m.probe ? PROBE_WORDS[m.probe.status] ?? m.probe.status : "sin probar");

export function RunBoard({
  changeId,
  onRemoved,
  onRestore,
}: {
  changeId: string;
  /** el CR se archivo o elimino: la lista se recarga */
  onRemoved?: () => void;
  /** "Deshacer" del archivado */
  onRestore?: (id: string) => void;
}) {
  const { meta, tick, probeTick, signer, notify, openFile, session } = useApp();
  const run = useLoad(() => api.run(changeId), [changeId, tick]);
  // que se puede hacer con el CR (editar antes del intake, archivar/eliminar al terminar)
  const life = useLoad(() => api.cr(changeId), [changeId, tick, run.data?.state.status, run.data?.busy]);
  const agents = session?.project?.config.agents;
  const inUse = useLoad(() => api.modelsInUse(), [probeTick, JSON.stringify(agents)]);
  const [mode, setModeState] = React.useState<Mode>(readMode);
  const setMode = (m: Mode) => {
    setModeState(m);
    try {
      localStorage.setItem(LS_MODE, m);
    } catch {
      /* sin almacenamiento: solo esta sesion */
    }
  };
  const [pausing, setPausing] = React.useState(false);
  const [tab, setTab] = React.useState("artifacts");
  const [artifact, setArtifact] = React.useState<string | null>(null);
  const [starting, setStarting] = React.useState(false);
  const [retrying, setRetrying] = React.useState(false);
  const events = useActivity(changeId, tick, run.data?.state.attempt ?? 1);

  if (run.error && !run.data) {
    return <div className="p-6"><Callout tone="danger" title="No se pudo cargar el run">{run.error}</Callout></div>;
  }
  const d = run.data;
  if (!d || !meta) return <LoadingBlock label="Cargando run…" lines={5} />;

  const st = d.state;
  // un ESCALATED por ambiguedad con su gate abierto se reanuda: no es terminal en la UI
  const resumable = st.status === "ESCALATED" && Boolean(st.awaiting_gate);
  const terminal = TERMINAL.includes(st.status) && !resumable;
  const retryable = RETRYABLE.includes(st.status) && !d.busy;
  const attempts = session?.project?.config.limits?.attempts ?? 3;
  const signature = signer.profile ? { profile_id: signer.profile.id, role: signer.role } : null;

  const models = inUse.data?.items ?? [];
  const notOk = mode === "adk" ? models.filter((m) => m.probe?.status !== "OK") : [];
  const paused = st.paused && !d.busy ? st.paused : null;
  const isPausing = d.busy && (pausing || Boolean(d.outcome?.pausing));
  const realLabel = models.length
    ? `Modelo real · ${models[0].provider}/${models[0].model}${models.length > 1 ? ` +${models.length - 1}` : ""}`
    : agents
      ? `Modelo real · ${agents.provider}/${agents.model}`
      : "Modelo real";

  const advance = async () => {
    if (
      notOk.length &&
      !window.confirm(
        `${notOk.map((m) => `${m.provider}/${m.model}: ${probeWords(m)}`).join("\n")}\n\n` +
          "Este modelo no ha pasado la prueba y la fase puede fallar. ¿Avanzar de todos modos?",
      )
    )
      return;
    setStarting(true);
    try {
      await api.advance(changeId, mode);
      run.reload();
    } catch (err) {
      notify(errorMessage(err), "danger");
    } finally {
      setStarting(false);
    }
  };

  const pause = async () => {
    setPausing(true);
    try {
      await api.pause(changeId);
      run.reload();
    } catch (err) {
      notify(errorMessage(err), "danger");
    } finally {
      setPausing(false);
    }
  };

  const flow = deriveFlow(st, events, meta.phases, d.busy);

  const showArtifact = (name: string) => {
    setArtifact(name);
    setTab("artifacts");
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-5 py-3">
        <div className="min-w-0 flex-1 basis-64">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="font-mono text-base font-semibold">{changeId}</h2>
            <Badge tone={statusTone(st.status, st.awaiting_gate)}>
              <StatusIcon status={st.status} awaiting={st.awaiting_gate} paused={st.paused} />
              {statusLabel(st.status, st.awaiting_gate)}
            </Badge>
            {d.busy ? <Badge tone="info">{isPausing ? "pausando…" : "en curso"}</Badge> : null}
            {paused ? <Badge tone="warning"><Pause className="size-3" /> Pausada en {statusLabel(paused.status)}</Badge> : null}
            {(st.attempt ?? 1) > 1 ? <Badge tone={(st.attempt ?? 1) > attempts ? "warning" : "neutral"}>Intento {st.attempt}/{attempts}</Badge> : null}
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground" title={st.branch ?? undefined}>
            {st.branch ? <>rama <span className="font-mono">{st.branch}</span> · </> : null}
            iteraciones {st.iterations ?? 0}
          </p>
        </div>
        <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2">
          {life.data?.editable && !d.busy ? (
            <EditCrButton
              changeId={changeId}
              onSaved={() => {
                life.reload();
                run.reload();
              }}
            />
          ) : null}
          {retryable ? (
            <Button variant="outline" size="sm" onClick={() => setRetrying(true)} title="Editar el CR y correr un intento nuevo (el actual se archiva)">
              <RotateCcw /> Reintentar
            </Button>
          ) : null}
          {!terminal ? (
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
              <Select aria-label="Modo de ejecución" className="h-8 w-auto min-w-40 max-w-64 text-xs" value={mode} disabled={d.busy} onChange={(e) => setMode(e.target.value as Mode)}>
                <option value="adk">{realLabel}</option>
                <option value="fake">Simulación (Fake, sin modelo)</option>
              </Select>
              {d.busy ? (
                <Button
                  variant="outline"
                  onClick={pause}
                  loading={isPausing}
                  disabled={isPausing}
                  title="Corta ya la ejecución; al reanudar se repite la fase en curso desde su inicio"
                >
                  <Pause /> {isPausing ? "Pausando…" : "Pausar"}
                </Button>
              ) : null}
              <Button
                onClick={advance}
                loading={starting || d.busy}
                disabled={Boolean(st.awaiting_gate) && !d.gate?.recorded}
                title={st.awaiting_gate && !d.gate?.recorded ? "Decide el gate primero" : paused ? "Repite la fase pausada desde su inicio" : "Avanza hasta el siguiente gate"}
              >
                <Play /> {paused ? "Reanudar" : "Avanzar"}
              </Button>
            </div>
          ) : (
            <TerminalActions
              detail={d}
              archivable={Boolean(life.data?.archivable)}
              deletable={Boolean(life.data?.deletable)}
              reason={life.data?.reason}
              onChange={run.reload}
              onRemoved={onRemoved}
              onRestore={onRestore}
            />
          )}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="space-y-4 p-5">
          <Card className="px-3 py-3">
            <FlowDiagram
              flow={flow}
              phases={meta.phases}
              state={st}
              busy={d.busy}
              artifacts={d.artifacts.map((a) => a.name)}
              onOpenArtifact={showArtifact}
              recorded={d.gate?.recorded ?? null}
            />
          </Card>

          <NowStrip
            flow={flow}
            live={d.live}
            busy={d.busy}
            message={d.outcome?.kind === "running" ? d.outcome.message : undefined}
            recorded={d.gate?.recorded ?? null}
          />

          {!terminal && notOk.length ? <ModelWarning items={notOk} /> : null}
          {!terminal ? <EnvOverrideNote items={models} /> : null}

          <RunAlerts detail={d} clarificationCap={session?.project?.config.limits?.clarification_rounds ?? 3} />

          {d.gate ? (
            <GateCard
              key={d.gate.name}
              gate={d.gate}
              decisions={meta.decisions}
              signer={signer}
              pendingFindings={d.findings.pending.length}
              onOpenArtifact={showArtifact}
              onDecide={async ({ decision, reason }) => {
                if (!signature) return;
                try {
                  await api.decideGate(changeId, d.gate!.name, { decision, reason, ...signature });
                  notify(`Gate ${d.gate!.name}: ${decision} registrado. Pulsa Avanzar para continuar.`, "success");
                  run.reload();
                } catch (err) {
                  notify(errorMessage(err), "danger");
                }
              }}
            />
          ) : null}

          <FindingsTable
            items={d.findings.items}
            pending={d.findings.pending}
            verdicts={meta.verdicts}
            roles={d.gate?.findings_roles ?? []}
            signer={signer}
            editable={d.gate?.name === "review"}
            onSave={async (verdicts) => {
              if (!signature) return;
              try {
                const r = await api.decideFindings(changeId, { verdicts, ...signature });
                notify(r.pending.length ? `Guardado. Faltan ${r.pending.length} finding(s).` : "Todos los findings clasificados.", "success");
                run.reload();
              } catch (err) {
                notify(errorMessage(err), "danger");
              }
            }}
          />

          <Card>
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList>
                <TabsTrigger value="artifacts"><FileText /> Artefactos ({d.artifacts.length})</TabsTrigger>
                <TabsTrigger value="diff"><GitCompare /> Diff</TabsTrigger>
                <TabsTrigger value="activity"><Activity /> Actividad{events.length ? ` (${events.length})` : ""}</TabsTrigger>
                <TabsTrigger value="summary"><FileCode2 /> Estado</TabsTrigger>
              </TabsList>
              <TabsContent value="artifacts">
                <ArtifactsPane detail={d} selected={artifact} onSelect={setArtifact} />
              </TabsContent>
              <TabsContent value="diff">
                <DiffPane changeId={changeId} hasBranch={Boolean(st.branch)} tick={tick} onOpenWorktree={(p) => openFile(`run:${changeId}`, p)} worktree={Boolean(d.worktree)} />
              </TabsContent>
              <TabsContent value="activity">
                {events.length ? <ActivityLog events={events} busy={d.busy} /> : <Timeline detail={d} />}
              </TabsContent>
              <TabsContent value="summary">
                <StateSummary detail={d} />
              </TabsContent>
            </Tabs>
          </Card>
        </div>
      </div>
      <RetryDialog open={retrying} onOpenChange={setRetrying} state={st} onRetried={run.reload} />
    </div>
  );
}

function RunAlerts({ detail, clarificationCap }: { detail: RunDetail; clarificationCap: number }) {
  const st = detail.state;
  const o = detail.outcome;
  const alerts: React.ReactNode[] = [];
  const used = st.po_clarifications?.length ?? 0;
  if (st.status === "ESCALATED" && st.awaiting_gate) {
    alerts.push(
      <Callout key="stop" tone="warning" icon={<AlertTriangle />} title="Escalado: el agente pide una aclaración (reanudable)">
        <p>{st.stop_reason || "Sin motivo registrado."}</p>
        <p className="mt-1">
          Registra tu aclaración como decisión en el gate <span className="font-mono">{st.awaiting_gate}</span> y pulsa Avanzar, o
          edita el CR con Reintentar. Aclaraciones usadas: {used}/{clarificationCap}.
        </p>
      </Callout>,
    );
  } else if (st.status === "ESCALATED" || st.status === "NOT READY") {
    alerts.push(
      <Callout key="stop" tone="danger" icon={<AlertTriangle />} title={st.status === "ESCALATED" ? "Run escalado a un humano (no se reanuda)" : "No listo para merge"}>
        <p>{st.stop_reason || "Sin motivo registrado."}</p>
        <p className="mt-1">Usa Reintentar para editar el CR y correr un intento nuevo; este intento se archiva y su rama se conserva.</p>
      </Callout>,
    );
  } else if (st.status === "READY FOR HUMAN MERGE") {
    alerts.push(
      <Callout key="ready" tone="success" title="Listo para merge humano">
        La fábrica nunca hace merge: revisa la rama <span className="font-mono">{st.branch}</span> y haz el merge tú.
      </Callout>,
    );
  }
  if (st.paused && !detail.busy) {
    alerts.push(
      <Callout key="paused" tone="info" icon={<Pause />} title={`Run pausado en ${statusLabel(st.paused.status)}`}>
        Al reanudar se repite esa fase desde su inicio (último punto de control).
        {st.paused.worktree_reset ? " Los cambios a medias del Coder en el worktree del run se descartaron." : ""}
      </Callout>,
    );
  }
  if (o && o.kind !== "awaiting" && o.kind !== "done" && o.kind !== "paused" && o.kind !== "running") {
    const copy = OUTCOME_COPY[o.kind];
    alerts.push(
      <Callout key="outcome" tone={copy.tone} title={copy.title}>
        {o.message}
      </Callout>,
    );
  }
  return alerts.length ? <div className="space-y-2">{alerts}</div> : null;
}

/** Modelos en uso que no pasaron la prueba: avisar ANTES de gastar una fase. */
function ModelWarning({ items }: { items: ModelInUse[] }) {
  const { notify, setView, setSettingsTab } = useApp();
  const [probing, setProbing] = React.useState(false);
  const probeAll = async () => {
    setProbing(true);
    try {
      const results = await Promise.all(items.map((m) => api.probe(m.provider, m.model)));
      const ok = results.filter((r) => r.status === "OK").length;
      notify(ok === results.length ? "Modelos disponibles" : `${results.length - ok} modelo(s) siguen sin pasar la prueba`, ok === results.length ? "success" : "danger");
    } catch (err) {
      notify(errorMessage(err), "danger");
    } finally {
      setProbing(false);
    }
  };
  return (
    <Callout tone="warning" icon={<AlertTriangle />} title="Un modelo en uso no pasó la prueba">
      <ul className="mt-1 space-y-0.5">
        {items.map((m) => (
          <li key={`${m.provider}:${m.model}`}>
            <ProviderLogo provider={m.provider} decorative className="mr-1 inline size-3.5 align-[-2px]" />
            <span className="font-mono">{m.provider}/{m.model}</span> ({m.roles.join(", ")}): <strong>{probeWords(m)}</strong>
            {m.probe?.message ? <span className="text-muted-foreground"> — {m.probe.message}</span> : null}
          </li>
        ))}
      </ul>
      <div className="mt-2 flex gap-2">
        <Button size="xs" variant="outline" loading={probing} onClick={probeAll}>
          <FlaskConical /> Probar ahora
        </Button>
        <Button
          size="xs"
          variant="ghost"
          onClick={() => {
            setSettingsTab("agents");
            setView("settings");
          }}
        >
          <Settings2 /> Ir a Ajustes
        </Button>
      </div>
    </Callout>
  );
}

/** FACTORY_MODEL del entorno gana sobre lo elegido en Ajustes: que no sea silencioso. */
export function EnvOverrideNote({ items }: { items: ModelInUse[] }) {
  const hit = items.find((m) => m.env_override);
  if (!hit) return null;
  return (
    <Callout tone="warning" icon={<AlertTriangle />} title="FACTORY_MODEL del entorno reemplaza el modelo">
      Se usará <span className="font-mono">{hit.model}</span> en lugar de{" "}
      <span className="font-mono">{hit.configured_model}</span> ({hit.roles.map(roleLabel).join(", ")}). Quita{" "}
      <span className="font-mono">FACTORY_MODEL</span> del entorno o del <span className="font-mono">.env</span> y reinicia Studio para usar el de Ajustes.
    </Callout>
  );
}

function TerminalActions({
  detail,
  archivable,
  deletable,
  reason,
  onChange,
  onRemoved,
  onRestore,
}: {
  detail: RunDetail;
  archivable: boolean;
  deletable: boolean;
  reason?: string;
  onChange: () => void;
  onRemoved?: () => void;
  onRestore?: (id: string) => void;
}) {
  const { notify } = useApp();
  const sign = useSignature();
  const [busy, setBusy] = React.useState<string | null>(null);
  const [deleting, setDeleting] = React.useState(false);
  const id = detail.change_id;
  const act = async (name: string, fn: () => Promise<string>) => {
    setBusy(name);
    try {
      notify(await fn(), "success");
      onChange();
    } catch (err) {
      notify(errorMessage(err), "danger");
    } finally {
      setBusy(null);
    }
  };
  const archive = async () => {
    const sig = sign();
    if (!sig) return;
    setBusy("archive");
    try {
      const r = await api.archiveCr(id, { profile_id: sig.profile_id, role: sig.role });
      notify(
        `${id} archivado${r.worktree_removed ? " (worktree quitado)" : ""}. La rama sigue.`,
        "success",
        onRestore ? { label: "Deshacer", onClick: () => onRestore(id) } : undefined,
      );
      onRemoved?.();
    } catch (err) {
      notify(errorMessage(err), "danger");
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
      <Button
        variant="outline"
        size="sm"
        loading={busy === "export"}
        onClick={() => act("export", async () => `Evidencia exportada en ${(await api.exportEvidence(id)).dest}`)}
      >
        <Archive /> Exportar evidencia
      </Button>
      <Button
        variant="outline"
        size="sm"
        loading={busy === "archive"}
        disabled={!archivable}
        title={archivable ? "Oculta el CR y su run; se puede restaurar. Quita el worktree, la rama sigue." : reason}
        onClick={archive}
      >
        <ArchiveIcon /> Archivar
      </Button>
      <Menu
        label={`Más acciones de ${id}`}
        items={[
          ...(detail.worktree
            ? [
                {
                  label: "Quitar worktree",
                  icon: <Trash2 />,
                  onSelect: () => {
                    if (!window.confirm(`Quitar el worktree de ${id}? La rama se conserva.`)) return;
                    act("cleanup", async () => ((await api.cleanup(id)).removed ? "Worktree eliminado (la rama sigue)" : "No había worktree"));
                  },
                },
              ]
            : []),
          {
            label: "Eliminar…",
            icon: <Trash2 />,
            danger: true,
            disabled: !deletable,
            hint: deletable ? "Borra el CR, su evidencia y el worktree; la rama sigue" : reason,
            onSelect: () => setDeleting(true),
          },
        ]}
      />
      <DeleteCrDialog
        changeId={id}
        hasRun
        open={deleting}
        onOpenChange={setDeleting}
        onDeleted={() => onRemoved?.()}
      />
    </div>
  );
}

function ArtifactsPane({ detail, selected, onSelect }: { detail: RunDetail; selected: string | null; onSelect: (n: string) => void }) {
  const current = selected ?? detail.artifacts[0]?.name ?? null;
  const content = useLoad(
    () => (current ? api.artifact(detail.change_id, current) : Promise.resolve(null)),
    [detail.change_id, current, detail.artifacts.find((a) => a.name === current)?.modified],
  );
  if (!detail.artifacts.length) {
    return <EmptyState icon={<FileText />} title="Aún no hay artefactos" description="Pulsa Avanzar para ejecutar la primera fase." />;
  }
  const text = content.data?.content ?? "";
  const isMd = current?.endsWith(".md");
  return (
    <div className="grid min-h-[22rem] grid-cols-[minmax(12rem,17rem)_minmax(0,1fr)]">
      <div className="anim-list max-h-[34rem] overflow-auto border-r border-border p-1.5">
        {detail.artifacts.map((a, i) => (
          <button
            key={a.name}
            style={{ ["--i" as string]: i }}
            type="button"
            onClick={() => onSelect(a.name)}
            title={a.name}
            className={cn("flex w-full min-w-0 items-center gap-1.5 rounded px-2 py-1.5 text-left font-mono text-[11px] transition-colors duration-100 hover:bg-accent", a.name === current && "bg-primary/15 text-foreground")}
          >
            <FileText className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{a.name}</span>
          </button>
        ))}
      </div>
      <div className="min-w-0 overflow-auto p-4">
        {content.error ? (
          <Callout tone="danger">{content.error}</Callout>
        ) : isMd ? (
          <div className="prose-factory">
            <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
          </div>
        ) : (
          <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed">{text}</pre>
        )}
      </div>
    </div>
  );
}

function DiffPane({
  changeId,
  hasBranch,
  tick,
  worktree,
  onOpenWorktree,
}: {
  changeId: string;
  hasBranch: boolean;
  tick: number;
  worktree: boolean;
  onOpenWorktree: (path: string) => void;
}) {
  const { theme } = useApp();
  const files = useLoad(() => (hasBranch ? api.diff(changeId) : Promise.resolve(null)), [changeId, hasBranch, tick]);
  const [selected, setSelected] = React.useState<string | null>(null);
  const current = selected ?? files.data?.files[0]?.path ?? null;
  const file = useLoad(() => (current ? api.diffFile(changeId, current) : Promise.resolve(null)), [changeId, current, tick]);

  if (!hasBranch) {
    return <EmptyState icon={<FolderGit2 />} title="Sin rama todavía" description="La rama y el worktree del run se crean al aprobar el gate plan." />;
  }
  if (files.error) return <div className="p-4"><Callout tone="danger">{files.error}</Callout></div>;
  if (files.data && !files.data.files.length) {
    return <EmptyState icon={<GitCompare />} title="Sin cambios en la rama" description="El Coder aún no ha confirmado cambios." />;
  }
  const statusTone = (s: string) => (s === "A" ? "text-success" : s === "D" ? "text-danger" : "text-warning");
  return (
    <div className="grid h-[30rem] grid-cols-[minmax(12rem,17rem)_minmax(0,1fr)]">
      <div className="overflow-auto border-r border-border p-1.5">
        {files.data?.files.map((f) => (
          <button
            key={f.path}
            type="button"
            onClick={() => setSelected(f.path)}
            className={cn("flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left font-mono text-[11px] hover:bg-accent", f.path === current && "bg-primary/15")}
            title={f.path}
          >
            <span className={cn("w-3 font-semibold", statusTone(f.status))}>{f.status}</span>
            <span className="truncate">{f.path}</span>
          </button>
        ))}
      </div>
      <div className="flex min-w-0 flex-col">
        {current ? (
          <div className="flex items-center justify-between border-b border-border px-3 py-1.5 text-xs">
            <span className="truncate font-mono">{current}</span>
            {worktree ? (
              <Button variant="ghost" size="xs" onClick={() => onOpenWorktree(current)}>
                Abrir en el worktree
              </Button>
            ) : null}
          </div>
        ) : null}
        <div className="min-h-0 flex-1">
          {file.data ? (
            <DiffEditor
              original={file.data.original}
              modified={file.data.modified}
              language={languageFor(file.data.path)}
              theme={theme === "dark" ? "vs-dark" : "vs"}
              options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12, automaticLayout: true, scrollBeyondLastLine: false }}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** Runs sin activity.jsonl (anteriores): linea de tiempo desde el disco (artefactos + gates). */
function Timeline({ detail }: { detail: RunDetail }) {
  const events = [...detail.artifacts]
    .sort((a, b) => b.modified - a.modified)
    .map((a) => ({ at: a.modified, text: a.name }));
  const gates = Object.entries(detail.state.human_gates ?? {});
  return (
    <div className="grid grid-cols-[1fr_16rem] gap-4 p-4">
      <ol className="relative space-y-3 border-l border-border pl-4">
        {detail.outcome ? (
          <li className="text-xs">
            <span className="absolute -left-[5px] mt-1 size-2.5 rounded-full bg-primary" />
            <span className="text-muted-foreground">{formatTime(detail.outcome.at)}</span> · motor:{" "}
            <span className="font-medium">{detail.outcome.kind}</span>
            {detail.outcome.message ? <span className="text-muted-foreground"> — {detail.outcome.message}</span> : null}
          </li>
        ) : null}
        {events.map((e) => (
          <li key={e.text} className="text-xs">
            <span className="absolute -left-[4px] mt-1 size-2 rounded-full bg-border" />
            <span className="text-muted-foreground"><Clock className="mr-1 inline size-3" />{formatTime(e.at)}</span> ·{" "}
            <span className="font-mono">{e.text}</span>
          </li>
        ))}
        {!events.length ? <li className="text-xs text-muted-foreground">Sin actividad todavía.</li> : null}
      </ol>
      <div className="space-y-1.5">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Gates humanos</p>
        {gates.map(([g, v]) => (
          <div key={g} className="flex items-center justify-between text-xs">
            <span className="font-mono">{g}</span>
            <Badge tone={v === "APPROVED" ? "success" : v === "PENDING" ? "neutral" : v === "REJECTED" ? "danger" : "warning"}>{v}</Badge>
          </div>
        ))}
      </div>
    </div>
  );
}

function StateSummary({ detail }: { detail: RunDetail }) {
  const st = detail.state;
  const rows: [string, React.ReactNode][] = [
    ["Verificación", st.verification ? `${st.verification.status} · tests ${st.verification.tests} · fallidos ${st.verification.failed} · lint ${st.verification.lint}` : "—"],
    ["Review", st.review ? `${st.review.status} · must fix ${st.review.must_fix} · should fix ${st.review.should_fix}` : "—"],
    ["Seguridad", st.security ? `${st.security.status} · high ${st.security.high} · medium ${st.security.medium}` : "—"],
    ["Worktree", detail.worktree ? <span className="font-mono">{detail.worktree}</span> : "—"],
    ...(st.po_clarifications?.length
      ? [["Aclaraciones PO", <ol key="c" className="list-decimal pl-4">{st.po_clarifications.map((c) => <li key={c.n}>{c.reason} <span className="text-muted-foreground">({c.by})</span></li>)}</ol>] as [string, React.ReactNode]]
      : []),
    ...(st.previous_attempts?.length
      ? [[
          "Intentos previos",
          <ul key="a" className="space-y-1">
            {st.previous_attempts.map((a) => (
              <li key={a.n}>
                <span className="font-medium">#{a.n} {statusLabel(a.status)}</span> — {a.stop_reason || "—"}
                <div className="text-muted-foreground">
                  archivo <span className="font-mono">{a.archive}</span>
                  {a.branch ? <> · rama <span className="font-mono">{a.branch}</span></> : null}
                </div>
              </li>
            ))}
          </ul>,
        ] as [string, React.ReactNode]]
      : []),
  ];
  return (
    <div className="space-y-4 p-4">
      <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-2 text-xs">
        {rows.map(([k, v]) => (
          <React.Fragment key={k}>
            <dt className="text-muted-foreground">{k}</dt>
            <dd>{v}</dd>
          </React.Fragment>
        ))}
      </dl>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">factory-state.json</summary>
        <pre className="mt-2 max-h-80 overflow-auto rounded-md bg-muted p-3 font-mono text-[11px]">{JSON.stringify(st, null, 2)}</pre>
      </details>
    </div>
  );
}
