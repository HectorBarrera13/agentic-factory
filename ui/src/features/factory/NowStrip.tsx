import { Hand, Loader2 } from "lucide-react";
import { ProviderLogo, PROVIDER_NAME } from "@/components/ProviderLogo";
import type { LiveWork } from "@/lib/api";
import { elapsedSince, formatDuration, toolPhrase, useNow, type Flow } from "@/lib/activity";
import { RoleIcon, roleInfo } from "@/lib/roles";

/** "Ahora": quien trabaja, con que modelo, en que y desde cuando (reemplaza el "Avanzando…"). */
export function NowStrip({ flow, live, busy, message }: { flow: Flow; live?: LiveWork | null; busy: boolean; message?: string }) {
  const node = flow.focus ? flow.byId[flow.focus] : null;
  const working = busy && node?.status === "running";
  const now = useNow(busy || node?.status === "waiting");

  if (node?.status === "waiting" && !busy) {
    const waited = elapsedSince(node.since, now);
    return (
      <div role="status" aria-live="polite" className="anim-pop flex items-center gap-3 rounded-xl border border-warning/40 bg-warning/10 px-4 py-2.5">
        <span className="anim-pulse flex size-9 items-center justify-center rounded-lg bg-warning/20 text-warning" style={{ ["--pulse" as string]: "var(--warning)" }}>
          <Hand className="size-5" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold">Esperando a {node.roles?.length ? node.roles.join(" / ") : "un humano"}</p>
          <p className="text-xs text-muted-foreground">
            {node.label}: revisa los artefactos y registra la decisión{waited !== undefined ? ` · hace ${formatDuration(waited)}` : ""}
          </p>
        </div>
      </div>
    );
  }
  if (!busy) return null;

  const role = live?.role ?? node?.role;
  const info = roleInfo(role);
  const provider = live?.provider ?? node?.provider;
  const model = live?.model ?? node?.model;
  const tool = node?.tool ?? (live?.tool ? { name: live.tool, target: live.target, running: true } : undefined);
  const since = live?.since ?? node?.since;
  const elapsed = elapsedSince(since, now);

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={working || live ? `Ahora: ${info.label} trabajando` : "Avanzando"}
      className="anim-pop relative flex items-center gap-3 overflow-hidden rounded-xl border bg-card px-4 py-2.5 shadow-sm"
      style={{ borderColor: `color-mix(in srgb, ${info.color} 45%, transparent)`, ["--pulse" as string]: info.color }}
    >
      {working || live ? (
        <RoleIcon role={role} size="lg" className="anim-pulse" />
      ) : (
        <span className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Loader2 className="size-5 animate-spin" />
        </span>
      )}
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-x-2 text-sm">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Ahora</span>
          <span className="font-semibold">{working || live ? `${info.label} trabajando` : "Avanzando…"}</span>
          {provider ? (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground" title={PROVIDER_NAME[provider] ?? provider}>
              <ProviderLogo provider={provider} className="size-3.5" />
              <span className="font-mono">{model ?? provider}</span>
            </span>
          ) : null}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {working || live ? toolPhrase(tool) || `${info.does}…` : message || "preparando la siguiente fase…"}
        </p>
      </div>
      {elapsed !== undefined ? <span className="font-mono text-sm tabular-nums text-foreground/80">{formatDuration(elapsed)}</span> : null}
      <span aria-hidden className="anim-shimmer absolute inset-x-0 bottom-0 h-0.5" />
    </div>
  );
}
