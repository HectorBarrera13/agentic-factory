import { cn } from "@/lib/utils";

const PRESETS = [
  { label: "Determinista", value: 0 },
  { label: "Bajo", value: 0.2 },
  { label: "Equilibrado", value: 0.7 },
];

/** Lo que realmente se envia (misma regla que providers.effective_temperature). */
export function effectiveTemperature(value: number, range: [number, number], supported: boolean | null | undefined) {
  if (supported === false) return null;
  return Math.min(Math.max(value, range[0]), range[1]);
}

export function TemperatureField({
  label,
  value,
  inherited,
  onChange,
  range,
  supported,
}: {
  label: string;
  /** null = hereda `inherited` */
  value: number | null;
  inherited?: number;
  onChange: (v: number | null) => void;
  /** rango nativo del proveedor efectivo */
  range: [number, number];
  /** resultado del probe para el modelo efectivo (null/undefined = sin probar) */
  supported?: boolean | null;
}) {
  const nullable = inherited !== undefined;
  const shown = value ?? inherited ?? 0;
  const applied = effectiveTemperature(shown, range, supported);
  const preset = (v: number) => (
    <button
      key={v}
      type="button"
      aria-pressed={value !== null && Math.abs(shown - v) < 1e-9}
      onClick={() => onChange(v)}
      className={cn(
        "rounded border px-1.5 py-0.5 text-[11px] transition-colors",
        value !== null && Math.abs(shown - v) < 1e-9 ? "border-primary bg-primary/15 text-primary" : "border-border hover:bg-accent",
      )}
    >
      {PRESETS.find((p) => p.value === v)?.label} {v}
    </button>
  );

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <input
          type="range"
          min={range[0]}
          max={range[1]}
          step={0.05}
          aria-label={label}
          value={Math.min(shown, range[1])}
          onChange={(e) => onChange(Number(e.target.value))}
          className={cn("min-w-24 flex-1 accent-[var(--primary)]", value === null && nullable && "opacity-50")}
        />
        <span className="flex shrink-0 items-center justify-end gap-1.5 whitespace-nowrap font-mono text-xs tabular-nums">
          {value === null && nullable ? (
            <span className="rounded-full border border-border bg-muted px-1.5 py-px font-sans text-[10px] text-muted-foreground">hereda</span>
          ) : null}
          {shown.toFixed(2)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {PRESETS.map((p) => preset(p.value))}
        {nullable && value !== null ? (
          <button type="button" onClick={() => onChange(null)} className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-accent">
            Heredar
          </button>
        ) : null}
        <span className="text-[11px] text-muted-foreground">
          rango del proveedor {range[0]}–{range[1]}
        </span>
      </div>
      {applied === null ? (
        <p className="text-[11px] text-warning">Este modelo no acepta temperatura: no se enviará (usa su valor fijo).</p>
      ) : applied !== shown ? (
        <p className="text-[11px] text-warning">Fuera del rango del proveedor: se aplicará {applied}.</p>
      ) : null}
    </div>
  );
}
