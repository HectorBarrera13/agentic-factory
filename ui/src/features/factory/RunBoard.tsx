import { DiffEditor } from "@monaco-editor/react";
import {
  AlertTriangle,
  Archive,
  Clock,
  FileCode2,
  FileText,
  FolderGit2,
  GitCompare,
  History,
  Play,
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
  Select,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui";
import { api, errorMessage, type RunDetail } from "@/lib/api";
import { useApp, useLoad } from "@/lib/app-state";
import { statusLabel, statusTone, TERMINAL } from "@/lib/phases";
import { cn, formatTime, languageFor } from "@/lib/utils";
import { FindingsTable } from "./FindingsTable";
import { GateCard } from "./GateCard";
import { PhaseStepper } from "./PhaseStepper";

const OUTCOME_COPY: Record<string, { tone: "info" | "warning" | "danger" | "success"; title: string }> = {
  running: { tone: "info", title: "Avanzando…" },
  awaiting: { tone: "warning", title: "Detenido en un gate humano" },
  blocked: { tone: "danger", title: "Bloqueado" },
  transient: { tone: "warning", title: "Error transitorio del modelo: reintenta más tarde" },
  error: { tone: "danger", title: "Error al avanzar (el run quedó en su último checkpoint)" },
  done: { tone: "success", title: "Run terminado" },
};

export function RunBoard({ changeId }: { changeId: string }) {
  const { meta, tick, signer, notify, openFile } = useApp();
  const run = useLoad(() => api.run(changeId), [changeId, tick]);
  const [mode, setMode] = React.useState<"fake" | "adk">("fake");
  const [tab, setTab] = React.useState("artifacts");
  const [artifact, setArtifact] = React.useState<string | null>(null);
  const [starting, setStarting] = React.useState(false);

  if (run.error && !run.data) {
    return <div className="p-6"><Callout tone="danger" title="No se pudo cargar el run">{run.error}</Callout></div>;
  }
  const d = run.data;
  if (!d || !meta) return <div className="p-6 text-sm text-muted-foreground">Cargando run…</div>;

  const st = d.state;
  const terminal = TERMINAL.includes(st.status);
  const signature = signer.profile ? { profile_id: signer.profile.id, role: signer.role } : null;

  const advance = async () => {
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

  const showArtifact = (name: string) => {
    setArtifact(name);
    setTab("artifacts");
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="font-mono text-base font-semibold">{changeId}</h2>
            <Badge tone={statusTone(st.status, st.awaiting_gate)}>{statusLabel(st.status, st.awaiting_gate)}</Badge>
            {d.busy ? <Badge tone="info">en curso</Badge> : null}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {st.branch ? <>rama <span className="font-mono">{st.branch}</span> · </> : null}
            iteraciones {st.iterations ?? 0}
          </p>
        </div>
        <div className="flex-1" />
        {!terminal ? (
          <div className="flex items-center gap-2">
            <Select aria-label="Modo de ejecución" className="h-8 w-40 text-xs" value={mode} onChange={(e) => setMode(e.target.value as "fake" | "adk")}>
              <option value="fake">Fake (determinista)</option>
              <option value="adk">ADK (Gemini)</option>
            </Select>
            <Button
              onClick={advance}
              loading={starting || d.busy}
              disabled={Boolean(st.awaiting_gate) && !d.gate?.recorded}
              title={st.awaiting_gate && !d.gate?.recorded ? "Decide el gate primero" : "Avanza hasta el siguiente gate"}
            >
              <Play /> Avanzar
            </Button>
          </div>
        ) : (
          <TerminalActions detail={d} onChange={run.reload} />
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="space-y-4 p-5">
          <Card className="px-4 py-4">
            <PhaseStepper phases={meta.phases} state={st} busy={d.busy} />
          </Card>

          <RunAlerts detail={d} />

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
                <TabsTrigger value="timeline"><History /> Línea de tiempo</TabsTrigger>
                <TabsTrigger value="summary"><FileCode2 /> Estado</TabsTrigger>
              </TabsList>
              <TabsContent value="artifacts">
                <ArtifactsPane detail={d} selected={artifact} onSelect={setArtifact} />
              </TabsContent>
              <TabsContent value="diff">
                <DiffPane changeId={changeId} hasBranch={Boolean(st.branch)} tick={tick} onOpenWorktree={(p) => openFile(`run:${changeId}`, p)} worktree={Boolean(d.worktree)} />
              </TabsContent>
              <TabsContent value="timeline">
                <Timeline detail={d} />
              </TabsContent>
              <TabsContent value="summary">
                <StateSummary detail={d} />
              </TabsContent>
            </Tabs>
          </Card>
        </div>
      </div>
    </div>
  );
}

function RunAlerts({ detail }: { detail: RunDetail }) {
  const st = detail.state;
  const o = detail.outcome;
  const alerts: React.ReactNode[] = [];
  if (st.status === "ESCALATED" || st.status === "NOT READY") {
    alerts.push(
      <Callout key="stop" tone="danger" icon={<AlertTriangle />} title={st.status === "ESCALATED" ? "Run escalado a un humano" : "No listo para merge"}>
        {st.stop_reason || "Sin motivo registrado."}
      </Callout>,
    );
  } else if (st.status === "READY FOR HUMAN MERGE") {
    alerts.push(
      <Callout key="ready" tone="success" title="Listo para merge humano">
        La fábrica nunca hace merge: revisa la rama <span className="font-mono">{st.branch}</span> y haz el merge tú.
      </Callout>,
    );
  }
  if (o && o.kind !== "awaiting" && o.kind !== "done" && !(o.kind === "running" && !detail.busy)) {
    const copy = OUTCOME_COPY[o.kind];
    alerts.push(
      <Callout key="outcome" tone={copy.tone} title={copy.title}>
        {o.message}
      </Callout>,
    );
  }
  return alerts.length ? <div className="space-y-2">{alerts}</div> : null;
}

function TerminalActions({ detail, onChange }: { detail: RunDetail; onChange: () => void }) {
  const { notify } = useApp();
  const [busy, setBusy] = React.useState<string | null>(null);
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
  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        loading={busy === "export"}
        onClick={() => act("export", async () => `Evidencia exportada en ${(await api.exportEvidence(detail.change_id)).dest}`)}
      >
        <Archive /> Exportar evidencia
      </Button>
      {detail.worktree ? (
        <Button
          variant="ghost"
          size="sm"
          loading={busy === "cleanup"}
          onClick={() => {
            if (!window.confirm(`Quitar el worktree de ${detail.change_id}? La rama se conserva.`)) return;
            act("cleanup", async () => ((await api.cleanup(detail.change_id)).removed ? "Worktree eliminado (la rama sigue)" : "No había worktree"));
          }}
        >
          <Trash2 /> Quitar worktree
        </Button>
      ) : null}
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
    <div className="grid min-h-[22rem] grid-cols-[15rem_1fr]">
      <div className="border-r border-border p-1.5">
        {detail.artifacts.map((a) => (
          <button
            key={a.name}
            type="button"
            onClick={() => onSelect(a.name)}
            className={cn("flex w-full items-center gap-1.5 truncate rounded px-2 py-1.5 text-left font-mono text-[11px] hover:bg-accent", a.name === current && "bg-primary/15")}
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
    <div className="grid h-[30rem] grid-cols-[15rem_1fr]">
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

/** Linea de tiempo desde el disco: artefactos por fecha + decisiones humanas + resultado. */
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
