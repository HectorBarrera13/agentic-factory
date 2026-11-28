import {
  Code2,
  Eye,
  FileCheck2,
  FlaskConical,
  ListChecks,
  Map,
  ScanSearch,
  ShieldCheck,
  UserCheck,
  type LucideIcon,
} from "lucide-react";
import { cn } from "./utils";

/** Rol de quien trabaja: los agentes del motor, la verificacion determinista y el humano. */
export type RoleId =
  | "intake"
  | "planner"
  | "coder"
  | "test_analyst"
  | "verify"
  | "reviewer"
  | "security"
  | "evidence"
  | "human";

export interface RoleInfo {
  label: string;
  /** Que hace, en una linea (tooltip y lector de pantalla). */
  does: string;
  icon: LucideIcon;
  /** Color del rol (variable CSS: cambia con el tema). */
  color: string;
}

export const ROLES: Record<RoleId, RoleInfo> = {
  intake: { label: "Intake", does: "analiza el Change Request", icon: ScanSearch, color: "var(--role-intake)" },
  planner: { label: "Planner", does: "propone el plan de implementación", icon: Map, color: "var(--role-planner)" },
  coder: { label: "Coder", does: "implementa el plan aprobado", icon: Code2, color: "var(--role-coder)" },
  test_analyst: { label: "Test Analyst", does: "busca casos sin cubrir y agrega tests", icon: FlaskConical, color: "var(--role-test)" },
  verify: { label: "Verificación", does: "corre tests y lint (determinista)", icon: ListChecks, color: "var(--role-verify)" },
  reviewer: { label: "Reviewer", does: "revisa el diff de forma independiente", icon: Eye, color: "var(--role-reviewer)" },
  security: { label: "Security", does: "revisa riesgos y corre los escáneres", icon: ShieldCheck, color: "var(--role-security)" },
  evidence: { label: "Evidencia", does: "consolida la evidencia del run", icon: FileCheck2, color: "var(--role-evidence)" },
  human: { label: "Humano", does: "decide en el gate", icon: UserCheck, color: "var(--role-human)" },
};

export function roleInfo(role: string | null | undefined): RoleInfo {
  return ROLES[(role ?? "") as RoleId] ?? { label: role || "—", does: "", icon: ListChecks, color: "var(--muted-foreground)" };
}

/** Icono del rol dentro de un cuadrito tintado con su color. */
export function RoleIcon({ role, className, size = "md" }: { role: string | null | undefined; className?: string; size?: "sm" | "md" | "lg" }) {
  const info = roleInfo(role);
  const Icon = info.icon;
  const box = size === "sm" ? "size-5 rounded" : size === "lg" ? "size-9 rounded-lg" : "size-7 rounded-md";
  const glyph = size === "sm" ? "size-3" : size === "lg" ? "size-5" : "size-4";
  return (
    <span
      aria-hidden
      className={cn("inline-flex shrink-0 items-center justify-center", box, className)}
      style={{ color: info.color, background: `color-mix(in srgb, ${info.color} 14%, transparent)` }}
    >
      <Icon className={glyph} />
    </span>
  );
}
