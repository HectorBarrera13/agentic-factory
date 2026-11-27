import Editor, { type OnMount } from "@monaco-editor/react";
import {
  ChevronDown,
  ChevronRight,
  Circle,
  File,
  FileLock2,
  Folder,
  FolderOpen,
  Lock,
  RefreshCw,
  Save,
  Search,
  X,
} from "lucide-react";
import * as React from "react";
import { Badge, Button, Callout, Dialog, DialogContent, EmptyState, Input, Select } from "@/components/ui";
import { api, errorMessage, type TreeEntry } from "@/lib/api";
import { useApp, useLoad } from "@/lib/app-state";
import { cn, languageFor } from "@/lib/utils";

interface OpenTab {
  key: string; // root::path
  root: string;
  path: string;
  content: string;
  saved: string;
  readonly: boolean;
  note?: string;
}

const PRE_WORKTREE = ["INTAKE_PENDING", "REQUIREMENTS_GATE", "PLAN_PENDING"];

function flatten(entries: TreeEntry[], out: string[] = []): string[] {
  for (const e of entries) {
    if (e.type === "file") out.push(e.path);
    else if (e.children) flatten(e.children, out);
  }
  return out;
}

function TreeNode({
  entry,
  depth,
  activePath,
  status,
  onOpen,
  expanded,
  toggle,
}: {
  entry: TreeEntry;
  depth: number;
  activePath?: string;
  status: Record<string, string>;
  onOpen: (path: string) => void;
  expanded: Set<string>;
  toggle: (path: string) => void;
}) {
  const isDir = entry.type === "dir";
  const open = expanded.has(entry.path);
  const st = status[entry.path];
  return (
    <>
      <button
        type="button"
        onClick={() => (isDir ? toggle(entry.path) : onOpen(entry.path))}
        className={cn(
          "flex w-full items-center gap-1 rounded-sm py-[3px] pr-2 text-left text-[13px] hover:bg-accent",
          activePath === entry.path && "bg-primary/15 text-foreground",
        )}
        style={{ paddingLeft: depth * 12 + 6 }}
        title={entry.path}
      >
        {isDir ? (
          open ? <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <span className="w-3.5 shrink-0" />
        )}
        {isDir ? (
          open ? <FolderOpen className="size-4 shrink-0 text-primary/80" /> : <Folder className="size-4 shrink-0 text-primary/80" />
        ) : entry.secret ? (
          <FileLock2 className="size-4 shrink-0 text-warning" />
        ) : (
          <File className="size-4 shrink-0 text-muted-foreground" />
        )}
        <span className={cn("truncate", st === "??" && "text-success", st && st !== "??" && "text-warning")}>{entry.name}</span>
        {st ? <span className="ml-auto text-[10px] font-semibold text-muted-foreground">{st === "??" ? "U" : st}</span> : null}
      </button>
      {isDir && open
        ? entry.children?.map((c) => (
            <TreeNode key={c.path} entry={c} depth={depth + 1} activePath={activePath} status={status} onOpen={onOpen} expanded={expanded} toggle={toggle} />
          ))
        : null}
    </>
  );
}

export function CodeView() {
  const { tick, notify, theme, pendingOpen, clearPendingOpen } = useApp();
  const [root, setRoot] = React.useState("user");
  const [tabs, setTabs] = React.useState<OpenTab[]>([]);
  const [active, setActive] = React.useState<string | null>(null);
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());
  const [quickOpen, setQuickOpen] = React.useState(false);
  const [saving, setSaving] = React.useState(false);

  const runs = useLoad(() => api.runs(), [tick]);
  const tree = useLoad(() => api.tree(root), [root]);
  const status = useLoad(() => (root === "user" ? api.gitStatus() : Promise.resolve({ files: [] })), [root, tick]);
  const statusMap = React.useMemo(() => {
    const m: Record<string, string> = {};
    for (const f of status.data?.files ?? []) {
      m[f.path] = f.status;
      const parts = f.path.split("/");
      for (let i = 1; i < parts.length; i++) m[parts.slice(0, i).join("/")] ??= "•";
    }
    return m;
  }, [status.data]);

  const worktreeRuns = (runs.data?.items ?? []).filter((r) => !PRE_WORKTREE.includes(r.status));
  const current = tabs.find((t) => t.key === active) ?? null;

  const openFile = React.useCallback(
    async (path: string, fileRoot = root) => {
      const key = `${fileRoot}::${path}`;
      if (tabs.some((t) => t.key === key)) {
        setActive(key);
        return;
      }
      try {
        const f = await api.readFile(fileRoot, path);
        const note = f.secret
          ? "Archivo de secretos: su contenido no se muestra ni se edita desde la UI."
          : f.binary
            ? "Archivo binario o demasiado grande para el editor."
            : f.readonly
              ? "Worktree del run: solo lectura (lo escribe la fábrica)."
              : undefined;
        setTabs((ts) => [...ts, { key, root: fileRoot, path, content: f.content, saved: f.content, readonly: f.readonly, note }]);
        setActive(key);
      } catch (err) {
        notify(errorMessage(err), "danger");
      }
    },
    [root, tabs, notify],
  );

  React.useEffect(() => {
    if (pendingOpen) {
      setRoot(pendingOpen.root);
      openFile(pendingOpen.path, pendingOpen.root);
      clearPendingOpen();
    }
  }, [pendingOpen, openFile, clearPendingOpen]);

  const save = React.useCallback(async () => {
    if (!current || current.readonly || current.content === current.saved) return;
    setSaving(true);
    try {
      await api.writeFile(current.root, current.path, current.content);
      setTabs((ts) => ts.map((t) => (t.key === current.key ? { ...t, saved: t.content } : t)));
      notify(`Guardado ${current.path}`, "success");
      status.reload();
    } catch (err) {
      notify(errorMessage(err), "danger");
    } finally {
      setSaving(false);
    }
  }, [current, notify, status]);

  const close = (key: string) => {
    const tab = tabs.find((t) => t.key === key);
    if (tab && tab.content !== tab.saved && !window.confirm(`${tab.path} tiene cambios sin guardar. ¿Cerrar?`)) return;
    const rest = tabs.filter((t) => t.key !== key);
    setTabs(rest);
    if (active === key) setActive(rest.at(-1)?.key ?? null);
  };

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
      } else if (mod && e.key.toLowerCase() === "p") {
        e.preventDefault();
        setQuickOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  const saveRef = React.useRef(save);
  saveRef.current = save;

  const onMount: OnMount = (editor, monaco) => {
    // Monaco captura el teclado: Ctrl+S / Ctrl+P se atienden aqui con la version vigente de save.
    editor.onKeyDown((e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.keyCode === monaco.KeyCode.KeyS) {
        e.preventDefault();
        e.stopPropagation();
        saveRef.current();
      } else if (e.keyCode === monaco.KeyCode.KeyP) {
        e.preventDefault();
        e.stopPropagation();
        setQuickOpen(true);
      }
    });
  };

  const toggle = (p: string) =>
    setExpanded((s) => {
      const n = new Set(s);
      if (n.has(p)) n.delete(p);
      else n.add(p);
      return n;
    });

  const allFiles = React.useMemo(() => flatten(tree.data?.entries ?? []), [tree.data]);

  return (
    <div className="flex h-full">
      <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-sidebar">
        <div className="space-y-2 border-b border-border p-2">
          <div className="flex items-center justify-between px-1">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Explorador</span>
            <div className="flex">
              <Button variant="ghost" size="icon" className="size-6" title="Buscar archivo (Ctrl+P)" onClick={() => setQuickOpen(true)}>
                <Search />
              </Button>
              <Button variant="ghost" size="icon" className="size-6" title="Recargar" onClick={() => { tree.reload(); status.reload(); }}>
                <RefreshCw />
              </Button>
            </div>
          </div>
          <Select aria-label="Raíz del explorador" className="h-8 text-xs" value={root} onChange={(e) => setRoot(e.target.value)}>
            <option value="user">Mi árbol (editable)</option>
            {worktreeRuns.map((r) => (
              <option key={r.change_id} value={`run:${r.change_id}`}>
                Worktree {r.change_id} (solo lectura)
              </option>
            ))}
          </Select>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-1">
          {tree.error ? (
            <div className="p-2">
              <Callout tone="warning">{tree.error}</Callout>
            </div>
          ) : tree.data?.entries.length === 0 ? (
            <p className="p-3 text-xs text-muted-foreground">Carpeta vacía.</p>
          ) : (
            tree.data?.entries.map((e) => (
              <TreeNode key={e.path} entry={e} depth={0} activePath={current?.root === root ? current.path : undefined} status={root === "user" ? statusMap : {}} onOpen={(p) => openFile(p)} expanded={expanded} toggle={toggle} />
            ))
          )}
          {tree.data?.truncated ? <p className="p-2 text-[11px] text-warning">Árbol truncado (demasiados archivos).</p> : null}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-9 shrink-0 items-stretch overflow-x-auto border-b border-border bg-sidebar">
          {tabs.map((t) => {
            const dirty = t.content !== t.saved;
            return (
              <div
                key={t.key}
                className={cn(
                  "group flex shrink-0 cursor-pointer items-center gap-1.5 border-r border-border px-3 text-xs",
                  t.key === active ? "bg-background text-foreground" : "text-muted-foreground hover:bg-accent",
                )}
                onClick={() => setActive(t.key)}
                title={`${t.root === "user" ? "" : `[${t.root}] `}${t.path}`}
              >
                {t.readonly ? <Lock className="size-3 text-muted-foreground" /> : null}
                <span>{t.path.split("/").pop()}</span>
                {t.root !== "user" ? <Badge tone="info" className="px-1.5 py-0 text-[9px]">{t.root.slice(4)}</Badge> : null}
                <button
                  type="button"
                  aria-label={`Cerrar ${t.path}`}
                  className="ml-1 rounded p-0.5 hover:bg-accent"
                  onClick={(e) => {
                    e.stopPropagation();
                    close(t.key);
                  }}
                >
                  {dirty ? <Circle className="size-2.5 fill-current group-hover:hidden" /> : null}
                  <X className={cn("size-3", dirty && "hidden group-hover:block")} />
                </button>
              </div>
            );
          })}
          <div className="flex-1" />
          {current && !current.readonly ? (
            <div className="flex items-center px-2">
              <Button size="xs" variant="ghost" onClick={save} loading={saving} disabled={current.content === current.saved} title="Guardar (Ctrl+S)">
                <Save /> Guardar
              </Button>
            </div>
          ) : null}
        </div>
        {current ? (
          <>
            {current.note ? (
              <div className="flex items-center gap-2 border-b border-border bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground">
                <Lock className="size-3.5" /> {current.note}
              </div>
            ) : null}
            <div className="min-h-0 flex-1">
              <Editor
                key={current.key}
                path={current.key}
                defaultLanguage={languageFor(current.path)}
                value={current.content}
                theme={theme === "dark" ? "vs-dark" : "vs"}
                onMount={onMount}
                onChange={(v) => setTabs((ts) => ts.map((t) => (t.key === current.key ? { ...t, content: v ?? "" } : t)))}
                options={{
                  readOnly: current.readonly,
                  minimap: { enabled: false },
                  fontSize: 13,
                  fontFamily: "JetBrains Mono, Cascadia Code, Consolas, monospace",
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  renderWhitespace: "selection",
                }}
              />
            </div>
          </>
        ) : (
          <EmptyState
            className="flex-1"
            icon={<File />}
            title="Ningún archivo abierto"
            description={
              <>
                Elige un archivo del explorador o pulsa <kbd className="rounded border px-1">Ctrl</kbd>+<kbd className="rounded border px-1">P</kbd>.
                Tu árbol es independiente de los runs: cada run trabaja en su propio worktree.
              </>
            }
          />
        )}
      </section>
      <QuickOpen open={quickOpen} onOpenChange={setQuickOpen} files={allFiles} onPick={(p) => openFile(p)} />
    </div>
  );
}

function QuickOpen({
  open,
  onOpenChange,
  files,
  onPick,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  files: string[];
  onPick: (p: string) => void;
}) {
  const [query, setQuery] = React.useState("");
  const [index, setIndex] = React.useState(0);
  React.useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
    }
  }, [open]);
  const results = React.useMemo(() => {
    const q = query.toLowerCase();
    return files
      .filter((f) => f.toLowerCase().includes(q))
      .sort((a, b) => a.length - b.length)
      .slice(0, 50);
  }, [files, query]);
  const pick = (p?: string) => {
    if (!p) return;
    onPick(p);
    onOpenChange(false);
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Ir a archivo" className="top-[18%] translate-y-0">
        <Input
          autoFocus
          placeholder="Nombre o ruta…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setIndex(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setIndex((i) => Math.min(i + 1, results.length - 1));
            if (e.key === "ArrowUp") setIndex((i) => Math.max(i - 1, 0));
            if (e.key === "Enter") pick(results[index]);
          }}
        />
        <div className="mt-2 max-h-80 overflow-auto">
          {results.map((f, i) => (
            <button
              key={f}
              type="button"
              onClick={() => pick(f)}
              className={cn("block w-full truncate rounded px-2 py-1.5 text-left font-mono text-xs", i === index ? "bg-primary/15" : "hover:bg-accent")}
            >
              {f}
            </button>
          ))}
          {results.length === 0 ? <p className="px-2 py-3 text-xs text-muted-foreground">Sin resultados.</p> : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
