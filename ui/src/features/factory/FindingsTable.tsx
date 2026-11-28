import { ChevronDown, ChevronRight, ListChecks, Plus, Save } from "lucide-react";
import * as React from "react";
import { Badge, Button, Callout, Card, CardHeader, Field, Input, Select, Textarea } from "@/components/ui";
import type { AddedFinding, Finding, FindingVerdict } from "@/lib/api";
import type { Signer } from "@/lib/app-state";
import { cn } from "@/lib/utils";

const VERDICT_LABEL: Record<string, string> = {
  ACCEPTED: "Aceptado",
  MODIFIED: "Modificado",
  REJECTED: "Rechazado",
  DEFERRED: "Diferido",
};
const FIX_VERDICTS = new Set(["ACCEPTED", "MODIFIED"]);
const SOURCE_LABEL: Record<string, string> = {
  reviewer: "Reviewer",
  human: "Human Reviewer",
  security: "Security",
  scanner: "Escáner",
};

function severityTone(s: string) {
  if (s === "CRITICAL" || s === "MUST FIX" || s === "HIGH") return "danger" as const;
  if (s === "SHOULD FIX" || s === "MEDIUM") return "warning" as const;
  return "neutral" as const;
}

interface Draft {
  decision: string;
  reason: string;
  blocking: boolean;
}

const saved = (f: Finding): Draft => ({ decision: f.decision ?? "", reason: f.reason ?? "", blocking: Boolean(f.human_blocking) });
const same = (a: Draft, b: Draft) => a.decision === b.decision && a.reason.trim() === b.reason.trim() && a.blocking === b.blocking;

/** Lo que se envia: el veredicto solo, o con motivo / "corregir en este ciclo". */
function toVerdict(d: Draft): FindingVerdict {
  const blocking = d.blocking && FIX_VERDICTS.has(d.decision);
  return d.reason.trim() || blocking ? { decision: d.decision, reason: d.reason.trim(), blocking } : d.decision;
}

/**
 * Clasificacion humana de findings (RF-/HR-/SEC-/SEC-SCAN-). Las decisiones se acumulan; el
 * gate review sigue bloqueado mientras quede alguno sin clasificar. Cada finding puede llevar
 * su motivo (llega al Coder Fix) y, si no es bloqueante, marcarse "corregir en este ciclo".
 * El Human Reviewer tambien puede añadir findings propios (HR-NN).
 */
export function FindingsTable({
  items,
  pending,
  verdicts,
  roles,
  signer,
  editable,
  onSave,
  onAdd,
}: {
  items: Finding[];
  pending: string[];
  verdicts: string[];
  roles: string[];
  signer: Signer;
  editable: boolean;
  onSave: (verdicts: Record<string, FindingVerdict>) => Promise<void>;
  onAdd?: (finding: AddedFinding) => Promise<void>;
}) {
  const [draft, setDraft] = React.useState<Record<string, Draft>>({});
  const [open, setOpen] = React.useState<Set<string>>(new Set());
  const [busy, setBusy] = React.useState(false);
  const [adding, setAdding] = React.useState(false);
  const roleOk = Boolean(signer.profile) && roles.includes(signer.role);
  const current = (f: Finding) => draft[f.id] ?? saved(f);
  const changed = items.filter((f) => draft[f.id] && draft[f.id].decision && !same(draft[f.id], saved(f)));

  if (!items.length && !(editable && onAdd)) return null;

  const edit = (f: Finding, patch: Partial<Draft>) => setDraft((d) => ({ ...d, [f.id]: { ...current(f), ...patch } }));
  const toggle = (id: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const save = async () => {
    setBusy(true);
    try {
      await onSave(Object.fromEntries(changed.map((f) => [f.id, toVerdict(draft[f.id])])));
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
          !items.length
            ? "Sin findings en esta ronda"
            : pending.length
              ? `${pending.length} de ${items.length} sin clasificar`
              : `Los ${items.length} findings están clasificados`
        }
        actions={
          editable ? (
            <div className="flex flex-wrap gap-2">
              {onAdd ? (
                <Button size="sm" variant="outline" onClick={() => setAdding((v) => !v)} disabled={!roleOk} aria-expanded={adding}>
                  <Plus /> Añadir finding
                </Button>
              ) : null}
              <Button size="sm" onClick={save} loading={busy} disabled={!changed.length || !roleOk}>
                <Save /> Guardar clasificación{changed.length ? ` (${changed.length})` : ""}
              </Button>
            </div>
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
      {adding && onAdd ? <AddFindingForm onCancel={() => setAdding(false)} onAdd={async (f) => { await onAdd(f); setAdding(false); }} /> : null}
      {items.length ? (
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
              const d = current(f);
              const canEscalate = !f.blocking && FIX_VERDICTS.has(d.decision);
              const fixes = FIX_VERDICTS.has(d.decision) && (f.blocking || d.blocking);
              return (
                <React.Fragment key={f.id}>
                  <tr className={cn("border-b border-border/60", !d.decision && "bg-warning/5")}>
                    <td className="px-3 py-1.5 align-top">
                      <button
                        type="button"
                        aria-label={isOpen ? `Ocultar ${f.id}` : `Ver ${f.id}`}
                        onClick={() => toggle(f.id)}
                        className="rounded p-0.5 text-muted-foreground hover:bg-accent"
                      >
                        {isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                      </button>
                    </td>
                    <td className="px-2 py-1.5 align-top">
                      <div className="font-mono text-xs">{f.id}</div>
                      {f.source ? <div className="text-[10px] text-muted-foreground">{SOURCE_LABEL[f.source] ?? f.source}</div> : null}
                    </td>
                    <td className="px-2 py-1.5 align-top">
                      <Badge tone={severityTone(f.severity)}>{f.severity}</Badge>
                      {f.blocking ? <span className="ml-1.5 text-[10px] text-danger">bloqueante</span> : null}
                    </td>
                    <td className="px-2 py-1.5 align-top">
                      {editable ? (
                        <div className="space-y-1">
                          <Select
                            aria-label={`Clasificar ${f.id}`}
                            className="h-7 w-48 text-xs"
                            value={d.decision}
                            disabled={!roleOk}
                            onChange={(e) => edit(f, { decision: e.target.value })}
                          >
                            <option value="">— sin clasificar —</option>
                            {verdicts.map((v) => (
                              <option key={v} value={v}>
                                {VERDICT_LABEL[v] ?? v}
                              </option>
                            ))}
                          </Select>
                          {canEscalate ? (
                            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                              <input
                                type="checkbox"
                                className="size-3.5 accent-[hsl(var(--primary))]"
                                checked={d.blocking}
                                disabled={!roleOk}
                                onChange={(e) => edit(f, { blocking: e.target.checked })}
                                aria-label={`Corregir ${f.id} en este ciclo`}
                              />
                              Corregir en este ciclo
                            </label>
                          ) : null}
                          {d.decision ? (
                            <div className="text-[10px] text-muted-foreground">{fixes ? "El Coder lo corrige en la ronda de fix" : "Queda registrado, no cambia código"}</div>
                          ) : null}
                        </div>
                      ) : (
                        <div className="text-xs text-muted-foreground">
                          {f.decision ? (VERDICT_LABEL[f.decision] ?? f.decision) : "—"}
                          {f.human_blocking ? <span className="ml-1 text-danger">· corregir en este ciclo</span> : null}
                        </div>
                      )}
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr className="border-b border-border/60 bg-muted/40">
                      <td />
                      <td colSpan={3} className="space-y-2 px-2 py-2">
                        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-foreground/80">{f.excerpt || "(sin extracto en los reportes)"}</pre>
                        {editable ? (
                          <Textarea
                            aria-label={`Motivo de ${f.id}`}
                            placeholder="Motivo de la clasificación (lo recibe el Coder si se corrige)"
                            className="min-h-16 text-xs"
                            value={d.reason}
                            disabled={!roleOk}
                            maxLength={2000}
                            onChange={(e) => edit(f, { reason: e.target.value })}
                          />
                        ) : f.reason ? (
                          <p className="break-words text-xs"><span className="text-muted-foreground">Motivo: </span>{f.reason}</p>
                        ) : null}
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}

/** Finding propio del Human Reviewer: queda como HR-NN, aceptado por quien lo añade. */
function AddFindingForm({ onAdd, onCancel }: { onAdd: (f: AddedFinding) => Promise<void>; onCancel: () => void }) {
  const [severity, setSeverity] = React.useState("MUST FIX");
  const [title, setTitle] = React.useState("");
  const [detail, setDetail] = React.useState("");
  const [ac, setAc] = React.useState("");
  const [blocking, setBlocking] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onAdd({ severity, title: title.trim(), detail: detail.trim(), ac: ac.trim(), blocking: severity !== "MUST FIX" && blocking });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form aria-label="Añadir finding" onSubmit={submit} className="anim-enter space-y-2 border-b border-border px-4 py-3">
      <div className="grid gap-2 sm:grid-cols-[10rem_1fr_8rem]">
        <Field label="Severidad">
          <Select aria-label="Severidad del finding" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="MUST FIX">MUST FIX</option>
            <option value="SHOULD FIX">SHOULD FIX</option>
          </Select>
        </Field>
        <Field label="Título">
          <Input aria-label="Título del finding" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} required />
        </Field>
        <Field label="AC (opcional)">
          <Input aria-label="AC del finding" value={ac} maxLength={40} placeholder="AC-01" onChange={(e) => setAc(e.target.value)} />
        </Field>
      </div>
      <Field label="Detalle">
        <Textarea aria-label="Detalle del finding" className="min-h-16 text-xs" value={detail} maxLength={2000} onChange={(e) => setDetail(e.target.value)} placeholder="Qué falla, dónde y cómo comprobarlo" />
      </Field>
      {severity !== "MUST FIX" ? (
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" className="size-3.5" checked={blocking} onChange={(e) => setBlocking(e.target.checked)} />
          Corregir en este ciclo
        </label>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Cancelar</Button>
        <Button type="submit" size="sm" loading={busy} disabled={!title.trim()}>
          <Plus /> Añadir
        </Button>
      </div>
    </form>
  );
}
