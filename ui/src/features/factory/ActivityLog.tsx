import {
  AlertTriangle,
  Check,
  Flag,
  Hand,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  Terminal,
  Wrench,
  X,
} from "lucide-react";
import * as React from "react";
import { ProviderLogo } from "@/components/ProviderLogo";
import type { ActivityEvent } from "@/lib/api";
import { formatDuration, toolPhrase } from "@/lib/activity";
import { RoleIcon, roleInfo } from "@/lib/roles";
import { cn } from "@/lib/utils";

const PHASE_ROLE: Record<string, string> = {
  intake: "intake",
  plan: "planner",
  implement: "coder",
  fix: "coder",
  test_analysis: "test_analyst",
  verify: "verify",
  review: "reviewer",
  security: "security",
  evidence: "evidence",
};

const DETAIL = new Set(["tool_start", "tool_end", "verify_cmd_start", "verify_cmd_end", "scan_start", "scan_end"]);

interface Group {
  key: number;
  start?: ActivityEvent;
  end?: ActivityEvent;
  items: ActivityEvent[];
}

const clock = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

function findLast(groups: Group[], phase: string): Group | undefined {
  for (let i = groups.length - 1; i >= 0; i--) if (groups[i].start?.phase === phase) return groups[i];
  return undefined;
}

/** Agrupa por fase: cada phase_start abre un bloque con sus tools/comandos hasta su fin. */
export function groupActivity(events: ActivityEvent[]): Group[] {
  const groups: Group[] = [];
  let open: Group | null = null;
  for (const e of events) {
    if (e.kind === "phase_start") {
      open = { key: e.seq, start: e, items: [] };
      groups.push(open);
    } else if ((e.kind === "phase_end" || e.kind === "phase_error") && open && open.start?.phase === e.phase) {
      open.end = e;
      open = null;
    } else if (open && (DETAIL.has(e.kind) || e.phase === open.start?.phase)) {
      open.items.push(e);
    } else if (DETAIL.has(e.kind) && e.phase && findLast(groups, e.phase)) {
      // p.ej. los escaneres corren justo despues de cerrar Security: van en su bloque
      findLast(groups, e.phase)!.items.push(e);
    } else {
      groups.push({ key: e.seq, items: [e] });
    }
  }
  return groups;
}

export function ActivityLog({ events, busy }: { events: ActivityEvent[]; busy: boolean }) {
  const [all, setAll] = React.useState(true);
  const box = React.useRef<HTMLDivElement>(null);
  const stick = React.useRef(true);
  const groups = React.useMemo(() => groupActivity(events), [events]);

  React.useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events.length, all]);

  return (
    <div className="p-4">
      <div className="mb-3 flex items-center gap-2">
        <p className="text-xs text-muted-foreground">
          {events.length} eventos del motor{busy ? " · en vivo" : ""}
        </p>
        {busy ? <span className="anim-breathe size-2 rounded-full bg-primary" aria-hidden /> : null}
        <div className="flex-1" />
        <div role="radiogroup" aria-label="Detalle de la actividad" className="flex rounded-md border border-border p-0.5 text-[11px]">
          {[
            [true, "Todo"],
            [false, "Solo agentes"],
          ].map(([v, t]) => (
            <button
              key={String(v)}
              type="button"
              role="radio"
              aria-checked={all === v}
              onClick={() => setAll(v as boolean)}
              className={cn("rounded px-2 py-0.5", all === v ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground")}
            >
              {t as string}
            </button>
          ))}
        </div>
      </div>
      <div
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="max-h-[30rem] overflow-auto pr-1"
      >
        <ol aria-label="Actividad del run" className="space-y-2">
          {groups.map((g) => (g.start ? <PhaseGroup key={g.key} group={g} all={all} /> : <EventRow key={g.key} e={g.items[0]} />))}
        </ol>
      </div>
    </div>
  );
}

function PhaseGroup({ group, all }: { group: Group; all: boolean }) {
  const s = group.start!;
  const role = s.role ?? PHASE_ROLE[s.phase ?? ""];
  const info = roleInfo(role);
  const end = group.end;
  const failed = end?.kind === "phase_error" || end?.ok === false;
  const running = !end;
  const items = all ? collapse(group.items) : [];
  return (
    <li
      className="anim-pop rounded-lg border bg-card"
      style={{ borderColor: `color-mix(in srgb, ${failed ? "var(--danger)" : info.color} ${running ? 55 : 25}%, transparent)` }}
      data-phase={s.phase}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <RoleIcon role={role} />
        <div className="min-w-0 flex-1">
          <p className="text-xs">
            <span className="font-semibold">{info.label}</span>
            <span className="text-muted-foreground"> · {s.phase === "fix" ? "corrige" : s.phase}</span>
          </p>
          {s.provider ? (
            <p className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <ProviderLogo provider={s.provider} className="size-3" />
              <span className="font-mono">{s.model ?? s.provider}</span>
            </p>
          ) : null}
        </div>
        <span className="text-[10px] text-muted-foreground">{clock(s.ts)}</span>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-1.5 py-px text-[10px] font-medium",
            running ? "bg-primary/15 text-primary" : failed ? "bg-danger/15 text-danger" : "bg-success/15 text-success",
          )}
        >
          {running ? <Loader2 className="size-3 animate-spin" /> : failed ? <X className="size-3" /> : <Check className="size-3" />}
          {running ? "en curso" : formatDuration(end?.duration_ms) || (failed ? "falló" : "listo")}
        </span>
      </div>
      {end && (end.message || end.recommendation || end.tests !== undefined) ? (
        <p className={cn("border-t border-border px-3 py-1 text-[11px]", failed ? "text-danger" : "text-muted-foreground")}>
          {end.message ??
            (end.tests !== undefined
              ? `${end.tests} test(s), ${end.failed ?? 0} fallido(s)`
              : `recomendación: ${String(end.recommendation)}`)}
        </p>
      ) : null}
      {items.length ? (
        <ul className="space-y-0.5 border-t border-border px-3 py-1.5">
          {items.map((e) => (
            <li key={e.seq} className="flex items-center gap-1.5 text-[11px]">
              {detailIcon(e)}
              <span className="truncate font-mono">{detailText(e)}</span>
              {e.duration_ms !== undefined ? <span className="ml-auto shrink-0 text-muted-foreground">{formatDuration(e.duration_ms)}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** Un tool o comando terminado reemplaza a su inicio: una linea por llamada. */
function collapse(items: ActivityEvent[]): ActivityEvent[] {
  const out: ActivityEvent[] = [];
  for (const e of items) {
    if (e.kind.endsWith("_end")) {
      const startKind = e.kind.replace("_end", "_start");
      const i = out.findIndex((p) => p.kind === startKind && (p.tool ?? p.command) === (e.tool ?? e.command));
      if (i >= 0) {
        const took = e.duration_ms ?? Math.max(0, Date.parse(e.ts) - Date.parse(out[i].ts));
        out[i] = { ...out[i], ...e, target: out[i].target ?? e.target, duration_ms: Number.isNaN(took) ? undefined : took };
        continue;
      }
    }
    out.push(e);
  }
  return out;
}

function detailIcon(e: ActivityEvent) {
  const cls = "size-3 shrink-0";
  if (e.kind.endsWith("_start")) return <Loader2 className={cn(cls, "animate-spin text-primary")} />;
  const bad = e.kind === "scan_end" ? Boolean(e.blocked) : e.ok === false;
  if (bad) return <X className={cn(cls, "text-danger")} />;
  if (e.kind.startsWith("verify_cmd")) return <Terminal className={cn(cls, "text-success")} />;
  if (e.kind.startsWith("scan")) return <ShieldCheck className={cn(cls, "text-success")} />;
  return <Wrench className={cn(cls, "text-muted-foreground")} />;
}

function detailText(e: ActivityEvent): string {
  if (e.kind.startsWith("verify_cmd")) return `${e.command}${e.returncode !== undefined && e.returncode !== null ? ` → ${e.returncode}` : ""}`;
  if (e.kind.startsWith("scan")) {
    if (e.kind === "scan_start") return "escáneres de seguridad…";
    const extra = e.degraded?.length ? ` (sin correr: ${e.degraded.join(", ")})` : "";
    return `escáneres de seguridad: ${e.blocked ? "bloquean el release" : "sin bloqueos"}${extra}`;
  }
  const phrase = toolPhrase({ name: e.tool ?? "", target: e.target, running: e.kind === "tool_start" }).replace(/^último: /, "");
  return e.error ? `${phrase} — ${e.error}` : phrase;
}

function EventRow({ e }: { e: ActivityEvent }) {
  const [icon, text, tone] = describe(e);
  return (
    <li className="anim-pop flex items-center gap-2 px-1 text-xs" data-kind={e.kind}>
      <span
        className={cn(
          "flex size-6 shrink-0 items-center justify-center rounded-full [&_svg]:size-3.5",
          tone === "warning" && "bg-warning/15 text-warning",
          tone === "danger" && "bg-danger/15 text-danger",
          tone === "success" && "bg-success/15 text-success",
          tone === "info" && "bg-primary/10 text-primary",
        )}
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">{text}</span>
      <span className="text-[10px] text-muted-foreground">{clock(e.ts)}</span>
    </li>
  );
}

type Tone = "info" | "warning" | "danger" | "success";

function describe(e: ActivityEvent): [React.ReactNode, React.ReactNode, Tone] {
  switch (e.kind) {
    case "gate_open":
      return [<Hand key="i" />, <>Gate <b>{e.gate}</b> abierto: espera a {e.roles?.join(" / ") || "un humano"}</>, "warning"];
    case "gate_decision": {
      const ok = e.decision === "APPROVED" || e.decision === "MODIFIED";
      return [ok ? <Check key="i" /> : <X key="i" />, <>Gate <b>{e.gate}</b>: {e.decision}{e.by ? <> · firmado por {e.by}</> : null}</>, ok ? "success" : "danger"];
    }
    case "fix_round":
      return [
        <RotateCcw key="i" />,
        <>Ronda de fix {e.n}{e.cap ? `/${e.cap}` : ""} por {e.fix === "verify" ? "verificación fallida" : `review${e.findings ? ` (${e.findings} finding(s))` : ""}`}: vuelve al Coder</>,
        "warning",
      ];
    case "paused":
      return [<Pause key="i" />, <>Run pausado{e.reason ? `: ${e.reason}` : ""}</>, "info"];
    case "resumed":
      return [<Play key="i" />, <>Run reanudado</>, "info"];
    case "escalated":
      return [<AlertTriangle key="i" />, <>Escalado a un humano: {e.reason}</>, "danger"];
    case "done":
      return [
        <Flag key="i" />,
        <>Run terminado: <b>{e.status}</b></>,
        e.status === "READY FOR HUMAN MERGE" ? "success" : "danger",
      ];
    default:
      return [<Wrench key="i" />, <span className="font-mono">{e.kind}{e.phase ? ` · ${e.phase}` : ""}</span>, "info"];
  }
}
