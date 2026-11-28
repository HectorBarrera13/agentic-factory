// Componentes base al estilo shadcn/ui (Radix + Tailwind + cva), escritos en el repo.
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Slot } from "@radix-ui/react-slot";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2, MoreHorizontal, X } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex shrink-0 select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition-[color,background-color,border-color,box-shadow,transform,opacity] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-sm hover:bg-primary/90 hover:shadow-md",
        secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        outline: "border border-border bg-card/40 hover:border-primary/40 hover:bg-accent",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        danger: "bg-danger text-white shadow-sm hover:bg-danger/90",
        "danger-outline": "border border-danger/40 text-danger hover:bg-danger/10",
        success: "bg-success text-white shadow-sm hover:bg-success/90",
      },
      size: {
        default: "h-9 px-3.5",
        sm: "h-8 px-2.5 text-xs",
        xs: "h-6 px-2 text-xs",
        icon: "h-8 w-8",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild, loading, children, disabled, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        {...props}
      >
        {loading ? <Loader2 className="animate-spin" /> : null}
        {children}
      </Comp>
    );
  },
);
Button.displayName = "Button";

const field =
  "w-full min-w-0 rounded-md border border-input bg-background px-3 py-1.5 text-sm shadow-xs transition-[border-color,box-shadow] duration-150 placeholder:text-muted-foreground hover:border-muted-foreground/40 focus-visible:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:opacity-60";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(field, "h-9", className)} {...props} />
  ),
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(field, "min-h-20 font-mono", className)} {...props} />
));
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, title, ...props }, ref) => {
  const inner = React.useRef<HTMLSelectElement | null>(null);
  const [shown, setShown] = React.useState("");
  React.useImperativeHandle(ref, () => inner.current as HTMLSelectElement);
  // el texto elegido entero en el tooltip: si no cabe se corta con elipsis
  React.useLayoutEffect(() => {
    const el = inner.current;
    setShown(el?.selectedOptions?.[0]?.text ?? "");
  });
  return (
    <select
      ref={inner}
      title={title ?? (shown || undefined)}
      className={cn(field, "h-9 cursor-pointer truncate pr-8", className)}
      {...props}
    />
  );
});
Select.displayName = "Select";

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn("text-xs font-medium text-muted-foreground", className)} {...props} />
  );
}

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Label>{label}</Label>
      {children}
      {hint ? <p className="text-[11px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

const badgeVariants = cva(
  "inline-flex min-w-0 max-w-full items-center gap-1 overflow-hidden rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap transition-colors duration-200 [&_svg]:shrink-0",
  {
    variants: {
      tone: {
        neutral: "border-border bg-muted text-muted-foreground",
        info: "border-info/30 bg-info/10 text-info",
        success: "border-success/30 bg-success/10 text-success",
        warning: "border-warning/30 bg-warning/10 text-warning",
        danger: "border-danger/30 bg-danger/10 text-danger",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

function textOf(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (React.isValidElement<{ children?: React.ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

/** Junta los textos seguidos en un solo <span truncate> (los iconos quedan fuera). */
function groupText(children: React.ReactNode): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let run: React.ReactNode[] = [];
  const flush = () => {
    if (run.length) out.push(<span key={`t${out.length}`} className="truncate">{run}</span>);
    run = [];
  };
  React.Children.toArray(children).forEach((c) => {
    if (typeof c === "string" || typeof c === "number") run.push(c);
    else {
      flush();
      out.push(c);
    }
  });
  flush();
  return out;
}

export function Badge({
  className,
  tone,
  children,
  title,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  const text = textOf(children).trim();
  return (
    <span className={cn(badgeVariants({ tone }), className)} title={title ?? (text || undefined)} {...props}>
      {groupText(children)}
    </span>
  );
}

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-lg border border-border bg-card text-card-foreground shadow-sm", className)}
      {...props}
    />
  );
}

export function CardHeader({
  title,
  description,
  actions,
  icon,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
      <div className="flex min-w-0 items-start gap-2.5">
        {icon ? <div className="mt-0.5 text-muted-foreground [&_svg]:size-4">{icon}</div> : null}
        <div className="min-w-0">
          <h3 className="text-sm font-semibold leading-tight">{title}</h3>
          {description ? (
            <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "anim-enter flex flex-col items-center justify-center gap-2 px-6 py-10 text-center",
        className,
      )}
    >
      {icon ? (
        <div className="mb-1 rounded-2xl bg-primary/10 p-3 text-primary ring-1 ring-primary/15 [&_svg]:size-6">
          {icon}
        </div>
      ) : null}
      <p className="text-sm font-medium">{title}</p>
      {description ? (
        <p className="max-w-sm text-xs text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function Callout({
  tone = "info",
  title,
  children,
  icon,
}: {
  tone?: "info" | "warning" | "danger" | "success";
  title?: string;
  children?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  const tones = {
    info: "border-info/30 bg-info/5",
    warning: "border-warning/40 bg-warning/5",
    danger: "border-danger/40 bg-danger/5",
    success: "border-success/40 bg-success/5",
  };
  const text = { info: "text-info", warning: "text-warning", danger: "text-danger", success: "text-success" };
  return (
    <div role="status" className={cn("anim-enter flex gap-2.5 rounded-lg border px-3 py-2.5 text-sm", tones[tone])}>
      {icon ? <div className={cn("mt-0.5 shrink-0 [&_svg]:size-4", text[tone])}>{icon}</div> : null}
      <div className="min-w-0 flex-1 break-words">
        {title ? <p className={cn("font-medium", text[tone])}>{title}</p> : null}
        {children ? <div className="text-xs text-foreground/80 break-words">{children}</div> : null}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Dialog
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="dialog-overlay fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]" />
      <DialogPrimitive.Content
        className={cn(
          "dialog-content fixed left-1/2 top-1/2 z-50 flex max-h-[88vh] w-[min(640px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl focus:outline-none",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <DialogPrimitive.Title className="break-words text-base font-semibold">{title}</DialogPrimitive.Title>
            {description ? (
              <DialogPrimitive.Description className="mt-1 text-xs text-muted-foreground">
                {description}
              </DialogPrimitive.Description>
            ) : (
              <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
            )}
          </div>
          <DialogPrimitive.Close className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground" aria-label="Cerrar">
            <X className="size-4" />
          </DialogPrimitive.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">{children}</div>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

// ------------------------------------------------------------------ Tabs
export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn("no-scrollbar flex items-center gap-1 overflow-x-auto border-b border-border px-2", className)}
      {...props}
    />
  );
}

export function TabsTrigger({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "tab-trigger inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-t-md px-2.5 py-2 text-xs font-medium text-muted-foreground transition-colors duration-150 hover:bg-accent/50 hover:text-foreground data-[state=active]:text-foreground [&_svg]:size-3.5",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn("tab-content focus-visible:outline-none", className)} {...props} />;
}

// ------------------------------------------------------------------ Skeleton
/** Bloque de carga con brillo: reemplaza los "Cargando…" con la forma del contenido. */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div aria-hidden className={cn("skeleton h-4 rounded-md", className)} {...props} />;
}

/** Pantalla de carga generica: titulo, barra y unas lineas (con texto para lectores). */
export function LoadingBlock({ label = "Cargando…", lines = 4, className }: { label?: string; lines?: number; className?: string }) {
  return (
    <div role="status" aria-label={label} className={cn("space-y-3 p-5", className)}>
      <Skeleton className="h-6 w-48" />
      <Skeleton className="h-24 w-full rounded-lg" />
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className="h-3.5" style={{ width: `${88 - i * 13}%` }} />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  );
}

// ------------------------------------------------------------------ Menu
export interface MenuItem {
  label: string;
  icon?: React.ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  hint?: string;
}

/** Menu de acciones secundarias (⋯): teclado (flechas, Esc) y cierre al hacer click fuera. */
export function Menu({ label, items, className }: { label: string; items: MenuItem[]; className?: string }) {
  const [open, setOpen] = React.useState(false);
  const box = React.useRef<HTMLDivElement>(null);
  const button = React.useRef<HTMLButtonElement>(null);
  const id = React.useId();

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    box.current?.querySelector<HTMLButtonElement>("[role=menuitem]:not([disabled])")?.focus();
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const onKey = (e: React.KeyboardEvent) => {
    const all = [...(box.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not([disabled])") ?? [])];
    const i = all.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape") {
      setOpen(false);
      button.current?.focus();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? (i + 1) % all.length : (i - 1 + all.length) % all.length;
      all[next]?.focus();
    }
  };

  return (
    <div ref={box} className={cn("relative", className)} onKeyDown={onKey}>
      <Button
        ref={button}
        type="button"
        variant="ghost"
        size="icon"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        title={label}
        onClick={() => setOpen((v) => !v)}
      >
        <MoreHorizontal />
      </Button>
      {open ? (
        <div
          id={id}
          role="menu"
          aria-label={label}
          className="anim-menu absolute right-0 top-full z-30 mt-1 min-w-52 rounded-lg border border-border bg-popover p-1 shadow-xl"
        >
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              disabled={it.disabled}
              title={it.hint}
              onClick={() => {
                setOpen(false);
                it.onSelect();
              }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-accent focus:bg-accent focus:outline-none disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-3.5",
                it.danger && "text-danger hover:bg-danger/10 focus:bg-danger/10",
              )}
            >
              {it.icon}
              <span className="min-w-0 flex-1 truncate">{it.label}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ ConfirmDialog
/** Confirmacion de una accion irreversible: hay que teclear `word` (p.ej. el ID del CR). */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  word,
  actionLabel,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  description?: string;
  word: string;
  actionLabel: string;
  onConfirm: () => Promise<void> | void;
  children?: React.ReactNode;
}) {
  const [typed, setTyped] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    if (open) setTyped("");
  }, [open]);
  const ok = typed.trim().toUpperCase() === word.toUpperCase();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description} className="w-[min(480px,94vw)]">
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!ok) return;
            setBusy(true);
            try {
              await onConfirm();
              onOpenChange(false);
            } catch {
              /* el llamador ya aviso del error: el dialogo sigue abierto */
            } finally {
              setBusy(false);
            }
          }}
        >
          {children}
          <Field label={`Escribe ${word} para confirmar`}>
            <Input autoFocus aria-label={`Escribe ${word} para confirmar`} value={typed} onChange={(e) => setTyped(e.target.value)} className="font-mono" autoComplete="off" />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button type="submit" variant="danger" disabled={!ok} loading={busy}>
              {actionLabel}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ ListInput
/** Lista separada por comas: se edita como texto y se convierte al salir del campo. */
export function ListInput({ value, onChange, ...rest }: { value: string[]; onChange: (v: string[]) => void } & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const [text, setText] = React.useState(value.join(", "));
  React.useEffect(() => setText(value.join(", ")), [value]);
  return (
    <Input
      {...rest}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => onChange(text.split(",").map((s) => s.trim()).filter(Boolean))}
    />
  );
}
