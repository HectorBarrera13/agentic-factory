import { Pencil, Save, X } from "lucide-react";
import * as React from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button, Callout, ConfirmDialog, Dialog, DialogContent, Textarea } from "@/components/ui";
import { api, errorMessage, roleLabel } from "@/lib/api";
import { useApp } from "@/lib/app-state";

/** Editor lado a lado (markdown | vista previa). Lo usan el detalle del CR y el run sin empezar. */
export function CrEditor({ value, onChange, className }: { value: string; onChange: (v: string) => void; className?: string }) {
  return (
    <div className={className ?? "grid h-full grid-cols-2"}>
      <Textarea
        aria-label="Contenido del CR"
        className="h-full min-h-0 resize-none rounded-none border-0 border-r p-5 font-mono text-xs focus-visible:ring-0"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="prose-factory min-w-0 overflow-auto break-words p-5">
        <Markdown remarkPlugins={[remarkGfm]}>{value}</Markdown>
      </div>
    </div>
  );
}

/** Guarda el CR; si el servidor lo congelo entretanto, lo dice con su motivo. */
export function useSaveCr(changeId: string, onSaved: () => void) {
  const { notify } = useApp();
  const [busy, setBusy] = React.useState(false);
  const save = async (body: string) => {
    setBusy(true);
    try {
      await api.updateCr(changeId, body);
      notify("Change Request guardado", "success");
      onSaved();
      return true;
    } catch (err) {
      notify(errorMessage(err), "danger");
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { save, busy };
}

/** "Editar CR" de un run creado cuyo intake aun no corre: el cambio llega tambien a la copia del run. */
export function EditCrDialog({
  changeId,
  open,
  onOpenChange,
  onSaved,
}: {
  changeId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [body, setBody] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const { save, busy } = useSaveCr(changeId, onSaved);

  React.useEffect(() => {
    if (!open) return;
    setBody(null);
    setError(null);
    api
      .cr(changeId)
      .then((cr) => setBody(cr.body))
      .catch((err) => setError(errorMessage(err)));
  }, [open, changeId]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={`Editar ${changeId}`}
        description="Aún no corre el intake: el cambio se guarda en el CR y en la copia del run. Al pulsar Avanzar queda congelado."
        className="w-[min(1100px,96vw)]"
      >
        {error ? <Callout tone="danger">{error}</Callout> : null}
        <div className="h-[min(60vh,560px)] overflow-hidden rounded-lg border border-border">
          {body === null ? (
            <div className="flex h-full items-center justify-center text-xs text-muted-foreground">Cargando…</div>
          ) : (
            <CrEditor value={body} onChange={setBody} />
          )}
        </div>
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            <X /> Cancelar
          </Button>
          <Button
            loading={busy}
            disabled={!body?.trim()}
            onClick={async () => {
              if (body !== null && (await save(body))) onOpenChange(false);
            }}
          >
            <Save /> Guardar
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Boton "Editar CR" con su dialogo. */
export function EditCrButton({ changeId, onSaved }: { changeId: string; onSaved: () => void }) {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} title="El intake aún no corre: el CR se puede cambiar">
        <Pencil /> Editar CR
      </Button>
      <EditCrDialog changeId={changeId} open={open} onOpenChange={setOpen} onSaved={onSaved} />
    </>
  );
}

/** Firma del perfil activo; null si no hay perfil (se avisa al usuario). */
export function useSignature() {
  const { signer, notify } = useApp();
  return () => {
    if (!signer.profile) {
      notify("Elige un perfil para firmar: usa el selector de perfil en la barra inferior.", "danger");
      return null;
    }
    return { profile_id: signer.profile.id, role: signer.role, who: `${signer.profile.name} (${roleLabel(signer.role)})` };
  };
}

/** Eliminar un CR: teclear el ID. Borra CR, evidencia y worktree; la rama se conserva. */
export function DeleteCrDialog({
  changeId,
  open,
  onOpenChange,
  hasRun,
  onDeleted,
}: {
  changeId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  hasRun: boolean;
  onDeleted: () => void;
}) {
  const { notify } = useApp();
  const sign = useSignature();
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Eliminar ${changeId}`}
      description="Es permanente: no se puede deshacer."
      word={changeId}
      actionLabel="Eliminar para siempre"
      onConfirm={async () => {
        const sig = sign();
        if (!sig) throw new Error("sin firma");
        try {
          const r = await api.deleteCr(changeId, changeId, { profile_id: sig.profile_id, role: sig.role });
          notify(`${changeId} eliminado (${r.deleted.length} elemento(s)). La rama git se conserva.`, "success");
          onDeleted();
        } catch (err) {
          notify(errorMessage(err), "danger");
          throw err;
        }
      }}
    >
      <Callout tone="danger" title="Se borra">
        <ul className="list-disc space-y-0.5 pl-4">
          <li>el archivo del Change Request</li>
          {hasRun ? (
            <>
              <li>la carpeta del run con toda su evidencia (artefactos, actividad, gates)</li>
              <li>el worktree, si existe</li>
            </>
          ) : null}
        </ul>
        <p className="mt-1.5">La rama git se conserva.</p>
      </Callout>
    </ConfirmDialog>
  );
}
