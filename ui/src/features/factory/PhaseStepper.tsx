import { Check, Hand, Loader2, Pause, X } from "lucide-react";
import type { Phase, RunState } from "@/lib/api";
import { stepStates } from "@/lib/phases";
import { cn } from "@/lib/utils";

export function PhaseStepper({ phases, state, busy }: { phases: Phase[]; state: RunState; busy: boolean }) {
  const steps = stepStates(phases, state);
  const paused = Boolean(state.paused) && !busy;
  return (
    <ol aria-label="Fases del run" className="flex items-start overflow-x-auto pb-1">
      {phases.map((phase, i) => {
        const s = steps[i];
        const gate = Boolean(phase.gate);
        return (
          <li key={phase.id} data-state={s} data-paused={paused && s === "current" ? "" : undefined} className="flex min-w-[5.5rem] flex-1 flex-col items-center gap-1.5 text-center">
            <div className="flex w-full items-center">
              <span className={cn("h-px flex-1", i === 0 ? "bg-transparent" : steps[i - 1] === "done" ? "bg-primary" : "bg-border")} />
              <span
                className={cn(
                  "flex size-7 shrink-0 items-center justify-center border-2 text-[11px] font-semibold transition-colors",
                  gate ? "rotate-45 rounded-md" : "rounded-full",
                  s === "done" && "border-primary bg-primary text-primary-foreground",
                  s === "current" && "border-primary bg-primary/10 text-primary",
                  s === "waiting" && "border-warning bg-warning/15 text-warning",
                  s === "failed" && "border-danger bg-danger/15 text-danger",
                  s === "pending" && "border-border bg-card text-muted-foreground",
                )}
              >
                <span className={cn(gate && "-rotate-45", "[&_svg]:size-3.5")}>
                  {s === "done" ? (
                    <Check />
                  ) : s === "failed" ? (
                    <X />
                  ) : s === "waiting" ? (
                    <Hand />
                  ) : s === "current" && paused ? (
                    <Pause aria-label="pausada" />
                  ) : s === "current" && busy ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    i + 1
                  )}
                </span>
              </span>
              <span className={cn("h-px flex-1", i === phases.length - 1 ? "bg-transparent" : s === "done" ? "bg-primary" : "bg-border")} />
            </div>
            <span
              className={cn(
                "px-1 text-[11px] leading-tight",
                s === "pending" ? "text-muted-foreground" : "text-foreground",
                s === "waiting" && "font-semibold text-warning",
              )}
            >
              {phase.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
