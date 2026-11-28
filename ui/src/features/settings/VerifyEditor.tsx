import { Plus, Trash2 } from "lucide-react";
import { Button, Input, Select } from "@/components/ui";
import type { VerifyCommand } from "@/lib/api";

const KINDS = ["pytest", "jest", "ruff", "lint", "exit_code"];

export function cmdText(cmd: VerifyCommand["cmd"]): string {
  return Array.isArray(cmd) ? cmd.map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(" ") : cmd;
}

/** Comandos de verificacion: el texto se guarda tal cual; el backend lo parte con shlex. */
export function VerifyEditor({
  commands,
  onChange,
}: {
  commands: VerifyCommand[];
  onChange: (cmds: VerifyCommand[]) => void;
}) {
  const update = (i: number, patch: Partial<VerifyCommand>) =>
    onChange(commands.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  return (
    <div className="space-y-2">
      {commands.length === 0 ? (
        <p className="rounded-md border border-dashed border-border px-3 py-3 text-center text-xs text-muted-foreground">
          Sin comandos: la verificación de cada run fallará hasta que agregues uno.
        </p>
      ) : null}
      {commands.map((c, i) => (
        <div key={i} className="anim-enter grid grid-cols-[8rem_minmax(0,1fr)_9rem_auto] gap-2">
          <Input
            aria-label="Nombre"
            value={c.name}
            placeholder="nombre"
            onChange={(e) => update(i, { name: e.target.value })}
          />
          <Input
            aria-label="Comando"
            className="font-mono text-xs"
            value={cmdText(c.cmd)}
            placeholder="{python} -m pytest -q"
            onChange={(e) => update(i, { cmd: e.target.value })}
          />
          <Select aria-label="Parser" value={c.kind} onChange={(e) => update(i, { kind: e.target.value })}>
            {KINDS.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </Select>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Quitar comando"
            onClick={() => onChange(commands.filter((_, j) => j !== i))}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <div className="flex items-center justify-between">
        <p className="text-[11px] text-muted-foreground">
          <code>{"{python}"}</code> = el Python que ejecuta la fábrica. Parsers: pytest, jest, ruff,
          lint o exit_code.
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange([...commands, { name: "", cmd: "", kind: "exit_code" }])}
        >
          <Plus /> Comando
        </Button>
      </div>
    </div>
  );
}
