import { RotateCcw, Signature } from "lucide-react";
import * as React from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button, Callout, Dialog, DialogContent, Field, Textarea } from "@/components/ui";
import { ApiError, api, errorMessage, roleLabel, type RunState } from "@/lib/api";
import { useApp } from "@/lib/app-state";
import { statusLabel } from "@/lib/phases";

/**
 * Reintentar: nuevo intento del MISMO CR, editado. El intento anterior se archiva entero
 * (runs/<CR>/intentos/<n>/), su rama se conserva y el nuevo vuelve a pasar todos los gates.
 * Firma el Product Owner (roles del gate requirements).
 */
export function RetryDialog({
  open,
  onOpenChange,
  state,
  onRetried,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  state: RunState;
  onRetried: () => void;
}) {
  const { signer, session, notify } = useApp();
  const cid = state.change_id;
  const [body, setBody] = React.useState("");
  const [file, setFile] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const roles = session?.project?.config.gates?.roles?.requirements ?? ["product owner"];
  const roleOk = Boolean(signer.profile) && roles.includes(signer.role);
  const attempt = state.attempt ?? 1;
  const cap = session?.project?.config.limits?.attempts ?? 3;

  React.useEffect(() => {
    if (!open) return;
    setReason("");
    setError(null);
    api
      .cr(cid)
      .then((cr) => {
        setBody(cr.body);
        setFile(cr.file);
      })
      .catch((err) => setError(errorMessage(err)));
  }, [open, cid]);

  const send = async (force: boolean): Promise<void> => {
    if (!signer.profile) return;
    const next = await api.retry(cid, { body, reason: reason.trim(), profile_id: signer.profile.id, role: signer.role, force });
    notify(`${cid}: intento ${next.attempt} creado. Pulsa Avanzar para ejecutar el intake.`, "success");
    onOpenChange(false);
    onRetried();
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      try {
        await send(false);
      } catch (err) {
        if (err instanceof ApiError && err.code === "attempts_exhausted") {
          if (window.confirm(`${err.message}\n\n¿Reintentar de todos modos? Quedará registrado como decisión explícita.`)) {
            await send(true);
          }
          return;
        }
        throw err;
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={`Reintentar ${cid} · intento ${attempt + 1}`}
        description="Edita el Change Request y empieza un intento nuevo. El intento anterior se archiva completo y su rama se conserva; el nuevo vuelve a pasar por todos los gates."
        className="w-[min(1000px,96vw)]"
      >
        <form className="space-y-3" onSubmit={submit}>
          <Callout tone="danger" title={`Intento ${attempt}: ${statusLabel(state.status)}`}>
            {state.stop_reason || "Sin motivo registrado."}
          </Callout>
          {attempt >= cap ? (
            <Callout tone="warning" title={`Llevas ${attempt} de ${cap} intentos`}>
              Si el CR sigue sin salir, considera dividirlo o reescribir su alcance. Puedes reintentar igualmente: se te pedirá confirmación.
            </Callout>
          ) : null}
          <Field label={`Change Request${file ? ` (${file})` : ""}`}>
            <div className="grid h-72 grid-cols-2 overflow-hidden rounded-md border border-border">
              <Textarea
                aria-label="Contenido del CR"
                className="h-full resize-none rounded-none border-0 border-r p-3 text-xs focus-visible:ring-0"
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
              <div className="prose-factory overflow-auto p-3">
                <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
              </div>
            </div>
          </Field>
          <Field label="Motivo del reintento (obligatorio, queda en la evidencia)">
            <Textarea aria-label="Motivo del reintento" className="min-h-14 font-sans" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Qué cambiaste en el CR y por qué…" />
          </Field>
          {!signer.profile ? (
            <Callout tone="warning" title="Elige un perfil para firmar">Usa el selector de perfil en la barra inferior.</Callout>
          ) : !roleOk ? (
            <Callout tone="warning" title={`El rol «${roleLabel(signer.role)}» no puede reintentar`}>
              Reintentar cambia el alcance: firma con {roles.map(roleLabel).join(" / ")}.
            </Callout>
          ) : null}
          {error ? <Callout tone="danger">{error}</Callout> : null}
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
            <Button type="submit" loading={busy} disabled={!roleOk || !reason.trim() || !body.trim()}>
              <RotateCcw /> Reintentar
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
