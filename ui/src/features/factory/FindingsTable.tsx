import { ChevronDown, ChevronRight, ListChecks, Save } from "lucide-react";
import * as React from "react";
import { Badge, Button, Callout, Card, CardHeader, Select } from "@/components/ui";
import type { Finding } from "@/lib/api";
import type { Signer } from "@/lib/app-state";
import { cn } from "@/lib/utils";

const VERDICT_LABEL: Record<string, string> = {
  ACCEPTED: "Aceptado (corregir)",
  MODIFIED: "Modificado (corregir)",
  REJECTED: "Rechazado",
  DEFERRED: "Diferido",
};

function severityTone(s: string) {
  if (s === "CRITICAL" || s === "MUST FIX" || s === "HIGH") return "danger" as const;
  if (s === "SHOULD FIX" || s === "MEDIUM") return "warning" as const;
  return "neutral" as const;
}

/**
 * Clasificacion humana de findings (RF-/SEC-/SEC-SCAN-). Las decisiones se acumulan; el
 * gate review sigue bloqueado mientras quede alguno sin clasificar.
 */
export function FindingsTable({
  items,
  pending,
  verdicts,
  roles,
  signer,
  editable,
  onSave,
}: {
  items: Finding[];
  pending: string[];
  verdicts: string[];
  roles: string[];
  signer: Signer;
  editable: boolean;
  onSave: (verdicts: Record<string, string>) => Promise<void>;
}) {
  const [draft, setDraft] = React.useState<Record<string, string>>({});
  const [open, setOpen] = React.useState<Set<string>>(new Set());
  const [busy, setBusy] = React.useState(false);
  const roleOk = Boolean(signer.profile) && roles.includes(signer.role);
  const changed = Object.entries(draft).filter(([id, v]) => v && items.find((f) => f.id === id)?.decision !== v);

  if (!items.length) return null;

  const save = async () => {
    setBusy(true);
    try {
      await onSave(Object.fromEntries(changed));
      setDraft({});
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card aria-label="Findings">
      <CardHeader
        icon={<ListChecks />}
        title="Findings de review y seguridad"
        description={
          pending.length
            ? `${pending.length} de ${items.length} sin clasificar`
            : `Los ${items.length} findings están clasificados`
        }
        actions={
          editable ? (
            <Button size="sm" onClick={save} loading={busy} disabled={!changed.length || !roleOk}>
              <Save /> Guardar clasificación{changed.length ? ` (${changed.length})` : ""}
            </Button>
          ) : null
        }
      />
      {editable && !roleOk ? (
        <div className="px-4 pt-3">
          <Callout tone="warning" title="Tu rol no puede clasificar findings">
            Roles autorizados: {roles.join(", ")}.
          </Callout>
        </div>
      ) : null}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <th className="w-8 px-3 py-2" />
            <th className="px-2 py-2">ID</th>
            <th className="px-2 py-2">Severidad</th>
            <th className="px-2 py-2">Clasificación</th>
          </tr>
        </thead>
        <tbody>
          {items.map((f) => {
            const isOpen = open.has(f.id);
            const value = draft[f.id] ?? f.decision ?? "";
            return (
              <React.Fragment key={f.id}>
                <tr className={cn("border-b border-border/60", !f.decision && !draft[f.id] && "bg-warning/5")}>
                  <td className="px-3 py-1.5">
                    <button
                      type="button"
                      aria-label={isOpen ? `Ocultar ${f.id}` : `Ver ${f.id}`}
                      onClick={() =>
                        setOpen((s) => {
                          const n = new Set(s);
                          if (n.has(f.id)) n.delete(f.id);
                          else n.add(f.id);
                          return n;
                        })
                      }
                      className="rounded p-0.5 text-muted-foreground hover:bg-accent"
                    >
                      {isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                    </button>
                  </td>
                  <td className="px-2 py-1.5 font-mono text-xs">{f.id}</td>
                  <td className="px-2 py-1.5">
                    <Badge tone={severityTone(f.severity)}>{f.severity}</Badge>
                    {f.blocking ? <span className="ml-1.5 text-[10px] text-danger">bloqueante</span> : null}
                  </td>
                  <td className="px-2 py-1.5">
                    {editable ? (
                      <Select
                        aria-label={`Clasificar ${f.id}`}
                        className="h-7 w-48 text-xs"
                        value={value}
                        disabled={!roleOk}
                        onChange={(e) => setDraft((d) => ({ ...d, [f.id]: e.target.value }))}
                      >
                        <option value="">— sin clasificar —</option>
                        {verdicts.map((v) => (
                          <option key={v} value={v}>
                            {VERDICT_LABEL[v] ?? v}
                          </option>
                        ))}
                      </Select>
                    ) : (
                      <span className="text-xs text-muted-foreground">{f.decision ?? "—"}</span>
                    )}
                  </td>
                </tr>
                {isOpen ? (
                  <tr className="border-b border-border/60 bg-muted/40">
                    <td />
                    <td colSpan={3} className="px-2 py-2">
                      <pre className="whitespace-pre-wrap font-mono text-[11px] text-foreground/80">{f.excerpt || "(sin extracto en los reportes)"}</pre>
                    </td>
                  </tr>
                ) : null}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
