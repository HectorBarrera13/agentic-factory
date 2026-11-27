import { FileText, Hand, ShieldAlert, Signature } from "lucide-react";
import * as React from "react";
import { Badge, Button, Callout, Card, CardHeader, Field, Select, Textarea } from "@/components/ui";
import { roleLabel, type RunDetail } from "@/lib/api";
import type { Signer } from "@/lib/app-state";

const DECISION_LABEL: Record<string, string> = {
  APPROVED: "Aprobar",
  MODIFIED: "Aprobar con cambios",
  REJECTED: "Rechazar",
  DEFERRED: "Diferir",
};

export interface GateDecision {
  decision: string;
  reason: string;
}

/**
 * Tarjeta del gate abierto. La fabrica nunca interpreta silencio: la decision se registra
 * solo con el boton, firmada con el perfil y rol activos.
 */
export function GateCard({
  gate,
  decisions,
  signer,
  pendingFindings,
  onOpenArtifact,
  onDecide,
}: {
  gate: NonNullable<RunDetail["gate"]>;
  decisions: string[];
  signer: Signer;
  pendingFindings: number;
  onOpenArtifact: (name: string) => void;
  onDecide: (d: GateDecision) => Promise<void>;
}) {
  const [decision, setDecision] = React.useState("APPROVED");
  const [reason, setReason] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [changing, setChanging] = React.useState(false);

  if (gate.recorded && !changing) {
    const r = gate.recorded;
    return (
      <Card className="border-success/40" aria-label={`Gate ${gate.name}`}>
        <CardHeader
          icon={<Signature className="text-success" />}
          title={<>Decisión registrada · gate <span className="font-mono">{gate.name}</span></>}
          description="El motor la aplica al pulsar Avanzar. Hasta entonces puedes cambiarla."
          actions={<Badge tone={r.decision === "APPROVED" ? "success" : r.decision === "REJECTED" ? "danger" : "warning"}>{DECISION_LABEL[r.decision] ?? r.decision}</Badge>}
        />
        <div className="flex items-end justify-between gap-3 p-4 text-xs">
          <div className="min-w-0 space-y-1">
            <p className="text-muted-foreground">
              Firmado por <span className="font-medium text-foreground">{r.recorded_by}</span>
            </p>
            <p className="break-words">«{r.reason}»</p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setChanging(true)}>
            Cambiar decisión
          </Button>
        </div>
      </Card>
    );
  }

  const roleOk = Boolean(signer.profile) && gate.roles.includes(signer.role);
  const blockedByFindings = gate.name === "review" && pendingFindings > 0;
  const canSubmit = roleOk && reason.trim().length > 0 && !blockedByFindings && !busy;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    try {
      await onDecide({ decision, reason: reason.trim() });
      setReason("");
      setChanging(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="border-warning/50 ring-1 ring-warning/20" aria-label={`Gate ${gate.name}`}>
      <CardHeader
        icon={<Hand className="text-warning" />}
        title={<>Decisión humana requerida · gate <span className="font-mono">{gate.name}</span></>}
        description={gate.question}
        actions={<Badge tone="warning">esperando</Badge>}
      />
      <form className="space-y-3 p-4" onSubmit={submit}>
        {gate.artifacts.length ? (
          <div className="flex flex-wrap gap-1.5">
            {gate.artifacts.map((a) => (
              <Button key={a} type="button" variant="outline" size="xs" onClick={() => onOpenArtifact(a)}>
                <FileText /> {a}
              </Button>
            ))}
          </div>
        ) : null}
        {blockedByFindings ? (
          <Callout tone="warning" icon={<ShieldAlert />} title={`${pendingFindings} finding(s) sin clasificar`}>
            El gate review no se puede decidir hasta clasificar todos los findings.
          </Callout>
        ) : null}
        {!signer.profile ? (
          <Callout tone="warning" title="Elige un perfil para firmar">Usa el selector de perfil en la barra inferior.</Callout>
        ) : !roleOk ? (
          <Callout tone="warning" title={`El rol «${roleLabel(signer.role)}» no puede firmar este gate`}>
            Roles autorizados: {gate.roles.map(roleLabel).join(", ")}.
          </Callout>
        ) : null}
        <div className="grid grid-cols-[12rem_1fr] gap-3">
          <Field label="Decisión">
            <Select aria-label="Decisión" value={decision} onChange={(e) => setDecision(e.target.value)}>
              {decisions.map((d) => (
                <option key={d} value={d}>
                  {DECISION_LABEL[d] ?? d}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Motivo (obligatorio, queda en la evidencia)">
            <Textarea
              aria-label="Motivo"
              className="min-h-16 font-sans"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Por qué decides esto…"
            />
          </Field>
        </div>
        <div className="flex items-center justify-between">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Signature className="size-3.5" />
            {signer.profile ? (
              <>
                Firma: <span className="font-medium text-foreground">{signer.profile.name} ({roleLabel(signer.role)})</span>
              </>
            ) : (
              "Sin firma"
            )}
          </p>
          <Button
            type="submit"
            disabled={!canSubmit}
            loading={busy}
            variant={decision === "REJECTED" ? "danger" : decision === "APPROVED" ? "success" : "default"}
          >
            Registrar decisión
          </Button>
        </div>
      </form>
    </Card>
  );
}
