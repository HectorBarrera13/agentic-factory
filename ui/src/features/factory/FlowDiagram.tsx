import { Check, ChevronsDownUp, ChevronsUpDown, Flag, Hand, Loader2, Pause, X } from "lucide-react";
import * as React from "react";
import { ProviderLogo } from "@/components/ProviderLogo";
import type { Phase, RunState } from "@/lib/api";
import {
  elapsedSince,
  FLOW,
  FLOW_EDGES,
  formatDuration,
  toolPhrase,
  useNow,
  type Flow,
  type FlowNode,
  type NodeStatus,
} from "@/lib/activity";
import { RoleIcon, roleInfo } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { PhaseStepper } from "./PhaseStepper";

// ---------------------------------------------------------------- geometria fija
const AGENT_W = 156;
const AGENT_H = 104;
const GATE_W = 70;
const GAP = 20;
const STACK_GAP = 14;
const TOP = 46; // espacio para los arcos de fix
const BOTTOM = 14;
const GATE_BOX = 40;

const colWidth = (col: number) => {
  const kinds = FLOW.filter((n) => n.col === col).map((n) => n.kind);
  return kinds.includes("agent") || kinds.includes("check") ? AGENT_W : GATE_W;
};
const COLS = Math.max(...FLOW.map((n) => n.col)) + 1;
const COL_X: number[] = [];
{
  let x = 8;
  for (let c = 0; c < COLS; c++) {
    COL_X.push(x);
    x += colWidth(c) + GAP;
  }
}
const WIDTH = COL_X[COLS - 1] + colWidth(COLS - 1) + 8;
const HEIGHT = TOP + AGENT_H * 2 + STACK_GAP + BOTTOM;
const MID = TOP + AGENT_H + STACK_GAP / 2;

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

function boxOf(id: string): Box {
  const n = FLOW.find((f) => f.id === id)!;
  const w = colWidth(n.col);
  const x = COL_X[n.col];
  if (n.kind === "gate" || n.kind === "end") {
    // el rombo queda centrado; la caja incluye su rotulo debajo
    return { x, y: MID - GATE_BOX / 2, w, h: GATE_BOX };
  }
  const y = n.row === -1 ? TOP : n.row === 1 ? TOP + AGENT_H + STACK_GAP : MID - AGENT_H / 2;
  return { x, y, w, h: AGENT_H };
}

const STATUS_WORD: Record<NodeStatus, string> = {
  idle: "pendiente",
  running: "trabajando…",
  done: "listo",
  failed: "falló",
  waiting: "esperando decisión",
  paused: "pausado",
};

type EdgeState = "idle" | "done" | "active" | "failed";

function edgeState(from: FlowNode, to: FlowNode): EdgeState {
  if (to.status === "running" || to.status === "waiting") return "active";
  if (to.status === "failed" && from.status === "done") return "failed";
  if (from.status === "done" && to.status !== "idle") return "done";
  return "idle";
}

function edgePath(a: Box, b: Box): string {
  // apilados en la misma columna: linea vertical
  if (Math.abs(a.x - b.x) < 1) {
    const x = a.x + a.w / 2;
    return `M ${x} ${a.y + a.h} L ${x} ${b.y}`;
  }
  const x1 = a.x + a.w;
  const y1 = a.y + a.h / 2;
  const x2 = b.x;
  const y2 = b.y + b.h / 2;
  const dx = (x2 - x1) / 2;
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

function backPath(from: Box, to: Box, lift: number): string {
  const x1 = from.x + from.w / 2;
  const x2 = to.x + to.w / 2;
  const y1 = from.y;
  const y2 = to.y;
  const top = Math.min(y1, y2) - lift;
  return `M ${x1} ${y1} C ${x1} ${top}, ${x2} ${top}, ${x2} ${y2}`;
}

const LS_COMPACT = "factory.flow.compact";
/** Se encoge para caber en el ancho disponible hasta este limite; mas estrecho, se desplaza. */
const MIN_SCALE = 0.82;

function readCompact(): boolean {
  try {
    return localStorage.getItem(LS_COMPACT) === "1";
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------- componente
export function FlowDiagram({
  flow,
  phases,
  state,
  busy,
  artifacts,
  onOpenArtifact,
  recorded,
}: {
  flow: Flow;
  phases: Phase[];
  state: RunState;
  busy: boolean;
  artifacts: string[];
  onOpenArtifact: (name: string) => void;
  /** decision ya registrada en el gate abierto (falta Avanzar) */
  recorded?: { decision: string } | null;
}) {
  const [compact, setCompactState] = React.useState(readCompact);
  const setCompact = (v: boolean) => {
    setCompactState(v);
    try {
      localStorage.setItem(LS_COMPACT, v ? "1" : "0");
    } catch {
      /* sin almacenamiento */
    }
  };
  const anyRunning = flow.nodes.some((n) => n.status === "running" || n.status === "waiting");
  const now = useNow(anyRunning);
  const scroller = React.useRef<HTMLDivElement>(null);
  const [scale, setScale] = React.useState(1);

  React.useLayoutEffect(() => {
    const el = scroller.current;
    if (!el || compact) return;
    const fit = () => setScale(Math.min(1, Math.max(MIN_SCALE, (el.clientWidth - 2) / WIDTH)));
    fit();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [compact]);

  // el nodo activo siempre a la vista (el diagrama es mas ancho que la pantalla)
  React.useEffect(() => {
    const el = scroller.current;
    if (!el || !flow.focus || compact) return;
    const raw = boxOf(flow.focus);
    const b = { x: raw.x * scale, w: raw.w * scale };
    const left = b.x - el.clientWidth / 2 + b.w / 2;
    if (b.x < el.scrollLeft || b.x + b.w > el.scrollLeft + el.clientWidth) {
      el.scrollTo?.({ left: Math.max(0, left), behavior: "smooth" });
    }
  }, [flow.focus, compact, scale]);

  const toggle = (
    <button
      type="button"
      onClick={() => setCompact(!compact)}
      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
      aria-pressed={compact}
      title={compact ? "Ver el diagrama completo del flujo" : "Contraer a una tira de fases"}
    >
      {compact ? <ChevronsUpDown className="size-3.5" /> : <ChevronsDownUp className="size-3.5" />}
      {compact ? "Ver diagrama" : "Contraer"}
    </button>
  );

  if (compact) {
    return (
      <div className="space-y-1">
        <div className="flex justify-end">{toggle}</div>
        <PhaseStepper phases={phases} state={state} busy={busy} />
      </div>
    );
  }

  const loopFor = (fix: "verify" | "review") => flow.loops.find((l) => l.fix === fix);
  const vLoop = loopFor("verify");
  // sin arcos de fix no hace falta el aire de arriba: el diagrama sube y se acorta
  const lift = flow.loops.length ? 0 : TOP - 12;
  const rLoop = loopFor("review");

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-3 px-1">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Flujo del run</p>
        <Legend />
        <div className="flex-1" />
        {toggle}
      </div>
      <div ref={scroller} className="overflow-x-auto pb-1">
        <div style={{ width: WIDTH * scale, height: (HEIGHT - lift) * scale }}>
        <div
          role="group"
          aria-label="Diagrama del flujo del run"
          className="relative origin-top-left"
          style={{ width: WIDTH, height: HEIGHT, transform: `scale(${scale}) translateY(${-lift}px)` }}
        >
          <svg className="absolute inset-0" width={WIDTH} height={HEIGHT} aria-hidden>
            <defs>
              {(["idle", "done", "active", "failed"] as EdgeState[]).map((s) => (
                <marker key={s} id={`arrow-${s}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill={EDGE_COLOR[s]} />
                </marker>
              ))}
            </defs>
            {FLOW_EDGES.map(([a, b]) => {
              const s = edgeState(flow.byId[a], flow.byId[b]);
              return (
                <path
                  key={`${a}-${b}`}
                  d={edgePath(boxOf(a), boxOf(b))}
                  data-edge={`${a}-${b}`}
                  data-state={s}
                  fill="none"
                  stroke={EDGE_COLOR[s]}
                  strokeWidth={s === "idle" ? 1.25 : 2}
                  strokeDasharray={s === "idle" ? "4 5" : undefined}
                  className={cn(s === "active" && "anim-flow")}
                  markerEnd={`url(#arrow-${s})`}
                />
              );
            })}
            {vLoop ? <BackEdge from="verify" to="coder" lift={26} loop={vLoop} /> : null}
            {rLoop ? <BackEdge from="g_review" to="coder" lift={42} loop={rLoop} /> : null}
          </svg>
          {flow.nodes.map((n) => {
            const b = boxOf(n.id);
            const has = n.artifact && artifacts.includes(n.artifact) ? n.artifact : null;
            const open = has ? () => onOpenArtifact(has) : undefined;
            return n.kind === "gate" || n.kind === "end" ? (
              <GateNode key={n.id} node={n} box={b} now={now} onOpen={open} recorded={n.status === "waiting" ? recorded : null} />
            ) : (
              <AgentNode key={n.id} node={n} box={b} now={now} onOpen={open} />
            );
          })}
        </div>
        </div>
      </div>
    </div>
  );
}

const EDGE_COLOR: Record<EdgeState, string> = {
  idle: "var(--border)",
  done: "color-mix(in srgb, var(--primary) 70%, transparent)",
  active: "var(--primary)",
  failed: "var(--danger)",
};

function BackEdge({ from, to, lift, loop }: { from: string; to: string; lift: number; loop: { n: number; cap?: number; active: boolean } }) {
  const a = boxOf(from);
  const b = boxOf(to);
  const color = loop.active ? "var(--warning)" : "color-mix(in srgb, var(--warning) 55%, transparent)";
  const labelX = (a.x + a.w / 2 + b.x + b.w / 2) / 2;
  const labelY = Math.min(a.y, b.y) - lift * 0.75;
  const text = `fix ${loop.n}${loop.cap ? `/${loop.cap}` : ""}`;
  return (
    <g data-loop={from === "verify" ? "verify" : "review"} data-active={loop.active ? "" : undefined}>
      <path
        d={backPath(a, b, lift)}
        fill="none"
        stroke={color}
        strokeWidth={loop.active ? 2 : 1.5}
        className={cn(loop.active && "anim-flow")}
        strokeDasharray={loop.active ? undefined : "3 4"}
        markerEnd="url(#arrow-active)"
      />
      <rect x={labelX - 26} y={labelY - 9} width={52} height={18} rx={9} fill="var(--card)" stroke={color} />
      <text x={labelX} y={labelY + 4} textAnchor="middle" fontSize="10" fontWeight={600} fill="var(--warning)">
        {text}
      </text>
    </g>
  );
}

function Legend() {
  const items: [string, string][] = [
    ["var(--primary)", "trabajando"],
    ["var(--warning)", "esperando / fix"],
    ["var(--success)", "listo"],
    ["var(--danger)", "falló"],
  ];
  return (
    <ul className="hidden items-center gap-3 text-[10px] text-muted-foreground md:flex">
      {items.map(([c, t]) => (
        <li key={t} className="flex items-center gap-1">
          <span className="size-2 rounded-full" style={{ background: c }} />
          {t}
        </li>
      ))}
    </ul>
  );
}

function statusIcon(s: NodeStatus) {
  if (s === "done") return <Check className="size-3" />;
  if (s === "failed") return <X className="size-3" />;
  if (s === "running") return <Loader2 className="size-3 animate-spin" />;
  if (s === "paused") return <Pause className="size-3" />;
  if (s === "waiting") return <Hand className="size-3" />;
  return null;
}

const CHIP: Record<NodeStatus, string> = {
  idle: "bg-muted text-muted-foreground",
  running: "bg-primary/15 text-primary",
  done: "bg-success/15 text-success",
  failed: "bg-danger/15 text-danger",
  waiting: "bg-warning/15 text-warning",
  paused: "bg-warning/15 text-warning",
};

function AgentNode({ node: n, box, now, onOpen }: { node: FlowNode; box: Box; now: number; onOpen?: () => void }) {
  const info = roleInfo(n.role);
  const elapsed = n.status === "running" ? elapsedSince(n.since, now) : undefined;
  const label = `${n.label} ${STATUS_WORD[n.status]}`;
  const accent = n.status === "failed" ? "var(--danger)" : n.status === "paused" ? "var(--warning)" : info.color;
  const Tag = onOpen ? "button" : "div";
  return (
    <Tag
      type={onOpen ? "button" : undefined}
      onClick={onOpen}
      aria-label={label}
      title={onOpen ? `${info.label}: ${info.does} · abrir ${n.artifact}` : `${info.label}: ${info.does}`}
      data-node={n.id}
      data-status={n.status}
      className={cn(
        "absolute flex flex-col overflow-hidden rounded-xl border bg-card text-left shadow-sm transition-[border-color,box-shadow,opacity] duration-300",
        onOpen && "cursor-pointer hover:shadow-md",
        n.status === "idle" && "border-border opacity-60",
        n.status === "running" && "anim-pulse",
        n.status === "paused" && "border-dashed",
      )}
      style={{
        left: box.x,
        top: box.y,
        width: box.w,
        height: box.h,
        borderColor: n.status === "idle" ? undefined : `color-mix(in srgb, ${accent} ${n.status === "running" ? 80 : 45}%, transparent)`,
        ["--pulse" as string]: accent,
      }}
    >
      <span aria-hidden className="h-1 w-full" style={{ background: n.status === "idle" ? "var(--border)" : accent, opacity: n.status === "done" ? 0.55 : 1 }} />
      <div className="flex min-h-0 flex-1 flex-col gap-1 px-2.5 py-1.5">
        <div className="flex items-center gap-1.5">
          <RoleIcon role={n.role} size="sm" />
          <span className="truncate text-xs font-semibold">{n.label}</span>
          {n.runs > 1 ? <span className="text-[10px] text-muted-foreground" title="veces que trabajó (rondas de fix incluidas)">×{n.runs}</span> : null}
          <span className={cn("ml-auto inline-flex shrink-0 items-center gap-0.5 whitespace-nowrap rounded-full px-1.5 py-px text-[10px] font-medium", CHIP[n.status])}>
            {statusIcon(n.status)}
            {n.status === "running" ? formatDuration(elapsed) || "0 s" : n.status === "done" && n.duration_ms !== undefined ? formatDuration(n.duration_ms) : STATUS_WORD[n.status].replace("…", "")}
          </span>
        </div>
        {n.commands?.length ? (
          <ul className="min-h-0 space-y-px overflow-hidden">
            {n.commands.slice(-3).map((c) => (
              <li key={c.command} className="flex items-center gap-1 text-[10px]" data-command={c.status}>
                {c.status === "running" ? (
                  <Loader2 className="size-2.5 shrink-0 animate-spin text-primary" />
                ) : c.status === "ok" ? (
                  <Check className="size-2.5 shrink-0 text-success" />
                ) : (
                  <X className="size-2.5 shrink-0 text-danger" />
                )}
                <span className="truncate font-mono">{c.command}</span>
                {c.duration_ms !== undefined ? <span className="ml-auto shrink-0 text-muted-foreground">{formatDuration(c.duration_ms)}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <>
            <div className="flex min-w-0 items-center gap-1 text-[10px] text-muted-foreground">
              {n.provider ? (
                <>
                  <ProviderLogo provider={n.provider} decorative className="size-3 shrink-0" />
                  <span className="truncate font-mono">{n.model ?? n.provider}</span>
                </>
              ) : (
                <span className="truncate">{info.does}</span>
              )}
            </div>
            <div className={cn("truncate text-[10px]", n.status === "failed" ? "text-danger" : "text-foreground/80")} title={n.message ?? toolPhrase(n.tool)}>
              {n.status === "failed" && n.message
                ? n.message
                : n.status === "running"
                  ? toolPhrase(n.tool) || "pensando…"
                  : n.status === "paused"
                    ? "pausado: se repite al reanudar"
                    : n.tool
                      ? toolPhrase(n.tool)
                      : ""}
            </div>
          </>
        )}
      </div>
      {n.status === "running" ? <span aria-hidden className="anim-shimmer h-0.5 w-full" /> : null}
    </Tag>
  );
}

function GateNode({
  node: n,
  box,
  now,
  onOpen,
  recorded,
}: {
  node: FlowNode;
  box: Box;
  now: number;
  onOpen?: () => void;
  recorded?: { decision: string } | null;
}) {
  const end = n.kind === "end";
  const waited = n.status === "waiting" ? elapsedSince(n.since, now) : undefined;
  const color =
    n.status === "done" ? "var(--success)" : n.status === "failed" ? "var(--danger)" : n.status === "waiting" ? "var(--warning)" : "var(--border)";
  const who = n.roles?.length ? n.roles.join(" / ") : "humano";
  const caption =
    n.status === "waiting" && recorded
      ? `${recorded.decision} registrado · pulsa Avanzar`
      : n.status === "waiting"
      ? `Esperando a ${who}`
      : n.decision
        ? `${n.decision}${n.by ? ` · ${n.by}` : ""}`
        : end && n.status === "done"
          ? "listo, merge tuyo"
          : end && n.status === "failed"
            ? "no listo"
            : "";
  const Tag = onOpen ? "button" : "div";
  return (
    <Tag
      type={onOpen ? "button" : undefined}
      onClick={onOpen}
      data-node={n.id}
      data-status={n.status}
      aria-label={`${n.label} ${STATUS_WORD[n.status]}${caption ? `: ${caption}` : ""}`}
      title={caption || n.label}
      className={cn("absolute flex flex-col items-center", onOpen && "cursor-pointer")}
      style={{ left: box.x, top: box.y, width: box.w }}
    >
      <span
        className={cn(
          "flex items-center justify-center border-2 bg-card transition-colors duration-300",
          end ? "rounded-full" : "rotate-45 rounded-lg",
          n.status === "waiting" && "anim-pulse",
          n.status === "idle" && "opacity-60",
        )}
        style={{
          width: end ? GATE_BOX : GATE_BOX - 8,
          height: end ? GATE_BOX : GATE_BOX - 8,
          marginTop: end ? 0 : 4,
          borderColor: color,
          background: n.status === "idle" ? undefined : `color-mix(in srgb, ${color} 14%, var(--card))`,
          color: n.status === "idle" ? "var(--muted-foreground)" : color,
          ["--pulse" as string]: color,
        }}
      >
        <span className={cn(!end && "-rotate-45", "[&_svg]:size-4")}>
          {end ? <Flag /> : n.status === "done" ? <Check /> : n.status === "failed" ? <X /> : <Hand />}
        </span>
      </span>
      <span className="mt-2 text-center text-[10px] font-medium leading-tight">{n.label}</span>
      {caption ? (
        <span className={cn("mt-0.5 line-clamp-2 text-center text-[9.5px] leading-tight", n.status === "waiting" ? "font-semibold text-warning" : "text-muted-foreground")}>
          {caption}
          {waited !== undefined ? ` · ${formatDuration(waited)}` : ""}
        </span>
      ) : null}
    </Tag>
  );
}
