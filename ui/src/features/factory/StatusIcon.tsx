import { AlertTriangle, CheckCircle2, CircleDashed, Hand, Loader2, Pause } from "lucide-react";
import { cn } from "@/lib/utils";

/** Icono del estado de un run: el mismo en la lista de CRs y en la cabecera del run. */
export function StatusIcon({
  status,
  awaiting,
  busy,
  paused,
  className,
}: {
  status: string;
  awaiting?: string;
  busy?: boolean;
  paused?: unknown;
  className?: string;
}) {
  const cls = cn("size-3 shrink-0", className);
  if (busy) return <Loader2 aria-hidden className={cn(cls, "animate-spin")} />;
  if (status === "READY FOR HUMAN MERGE") return <CheckCircle2 aria-hidden className={cls} />;
  if (awaiting) return <Hand aria-hidden className={cls} />;
  if (status === "ESCALATED" || status === "NOT READY") return <AlertTriangle aria-hidden className={cls} />;
  if (paused) return <Pause aria-hidden className={cls} />;
  return <CircleDashed aria-hidden className={cls} />;
}
