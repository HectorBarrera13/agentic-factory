import {
  AlertTriangle,
  CheckCircle2,
  Code2,
  Factory,
  FolderOpen,
  Info,
  Moon,
  Settings,
  Sun,
  UserRound,
} from "lucide-react";
import * as React from "react";
import { Button, Callout, Select } from "@/components/ui";
import { CodeView } from "@/features/code/CodeView";
import { FactoryView } from "@/features/factory/FactoryView";
import { OpenProjectDialog, WelcomeScreen } from "@/features/project/OpenProject";
import { SettingsView } from "@/features/settings/SettingsView";
import { roleLabel } from "@/lib/api";
import { useApp, type View } from "@/lib/app-state";
import { cn } from "@/lib/utils";

const ITEMS: { id: View; label: string; icon: React.ReactNode; key: string }[] = [
  { id: "code", label: "Código", icon: <Code2 />, key: "1" },
  { id: "factory", label: "Factory", icon: <Factory />, key: "2" },
  { id: "settings", label: "Ajustes", icon: <Settings />, key: "3" },
];

function ActivityBar() {
  const { view, setView, theme, toggleTheme } = useApp();
  return (
    <nav
      aria-label="Vistas"
      className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-border bg-sidebar py-2"
    >
      <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
        F
      </div>
      {ITEMS.map((item) => (
        <button
          key={item.id}
          type="button"
          title={`${item.label} (Alt+${item.key})`}
          aria-label={item.label}
          aria-current={view === item.id ? "page" : undefined}
          onClick={() => setView(item.id)}
          className={cn(
            "relative flex h-11 w-11 flex-col items-center justify-center gap-0.5 rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground [&_svg]:size-5",
            view === item.id && "bg-accent text-foreground",
          )}
        >
          {view === item.id ? (
            <span className="absolute -left-2 top-2 h-7 w-0.5 rounded-full bg-primary" />
          ) : null}
          {item.icon}
          <span className="text-[9px] font-medium">{item.label}</span>
        </button>
      ))}
      <div className="flex-1" />
      <button
        type="button"
        onClick={toggleTheme}
        title={theme === "dark" ? "Tema claro" : "Tema oscuro"}
        aria-label="Cambiar tema"
        className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground [&_svg]:size-4"
      >
        {theme === "dark" ? <Sun /> : <Moon />}
      </button>
    </nav>
  );
}

/** Indicador siempre visible de quien firma las decisiones humanas. */
export function SignerIndicator() {
  const { session, signer, setSigner, setView } = useApp();
  const profiles = session?.profiles ?? [];
  if (!profiles.length) {
    return (
      <button
        type="button"
        onClick={() => setView("settings")}
        className="flex items-center gap-1.5 rounded px-2 py-0.5 text-warning hover:bg-accent"
      >
        <UserRound className="size-3.5" /> Sin perfil: crea uno para firmar gates
      </button>
    );
  }
  return (
    <div className="flex items-center gap-1.5">
      <UserRound className="size-3.5 text-muted-foreground" />
      <Select
        aria-label="Perfil activo"
        className="h-6 w-auto border-transparent bg-transparent px-1 py-0 text-[11px] shadow-none"
        value={signer.profile?.id ?? ""}
        onChange={(e) => setSigner(e.target.value || null)}
      >
        <option value="">Elegir perfil…</option>
        {profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </Select>
      {signer.profile ? (
        <Select
          aria-label="Rol activo"
          className="h-6 w-auto border-transparent bg-transparent px-1 py-0 text-[11px] shadow-none"
          value={signer.role}
          onChange={(e) => setSigner(signer.profile!.id, e.target.value)}
        >
          {signer.profile.roles.map((r) => (
            <option key={r} value={r}>
              {roleLabel(r)}
            </option>
          ))}
        </Select>
      ) : null}
    </div>
  );
}

function StatusBar({ onOpenProject }: { onOpenProject: () => void }) {
  const { session, live } = useApp();
  const project = session?.project;
  return (
    <footer className="flex h-7 shrink-0 items-center gap-3 border-t border-border bg-sidebar px-3 text-[11px] text-muted-foreground">
      <button
        type="button"
        onClick={onOpenProject}
        className="flex items-center gap-1.5 rounded px-1.5 py-0.5 hover:bg-accent hover:text-foreground"
        title={project?.root}
      >
        <FolderOpen className="size-3.5" />
        {project ? project.name : "Sin proyecto"}
      </button>
      {project ? (
        <span>
          {project.config.code.language} · base {project.config.git.base_branch}
          {project.git ? "" : " · sin git"}
        </span>
      ) : null}
      <span className="flex items-center gap-1" title="Actualizaciones en vivo (SSE)">
        <span className={cn("size-1.5 rounded-full", live ? "bg-success" : "bg-muted-foreground")} />
        {live ? "en vivo" : "sin conexión en vivo"}
      </span>
      <div className="flex-1" />
      <SignerIndicator />
    </footer>
  );
}

function Toasts() {
  const { toasts } = useApp();
  return (
    <div className="pointer-events-none fixed bottom-10 right-4 z-[60] flex w-80 flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="alert"
          className={cn(
            "pointer-events-auto flex items-start gap-2 rounded-lg border bg-popover px-3 py-2.5 text-xs shadow-lg",
            t.tone === "danger" && "border-danger/40",
            t.tone === "success" && "border-success/40",
          )}
        >
          {t.tone === "danger" ? (
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" />
          ) : t.tone === "success" ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
          ) : (
            <Info className="mt-0.5 size-4 shrink-0 text-info" />
          )}
          <span className="break-words">{t.text}</span>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const { session, loadError, view, setView, refreshSession } = useApp();
  const [openDialog, setOpenDialog] = React.useState(false);
  const [browseFirst, setBrowseFirst] = React.useState(false);
  const openProject = (browse = false) => {
    setBrowseFirst(browse);
    setOpenDialog(true);
  };

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && ["1", "2", "3"].includes(e.key)) {
        e.preventDefault();
        setView(ITEMS[Number(e.key) - 1].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setView]);

  if (loadError && !session) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="w-full max-w-md space-y-3">
          <Callout tone="danger" title="No se pudo conectar con Factory Studio" icon={<AlertTriangle />}>
            {loadError}. Abre la URL con token que mostró la terminal (
            <code>python -m factory_ui</code>).
          </Callout>
          <Button variant="outline" onClick={() => refreshSession()}>
            Reintentar
          </Button>
        </div>
      </div>
    );
  }
  if (!session) {
    return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Cargando…</div>;
  }

  const project = session.project;
  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        <ActivityBar />
        <main className="min-w-0 flex-1">
          {!project ? (
            <WelcomeScreen onOpen={openProject} />
          ) : !project.configured && view !== "settings" ? (
            <WelcomeScreen onOpen={openProject} unconfigured />
          ) : view === "code" ? (
            <CodeView />
          ) : view === "factory" ? (
            <FactoryView />
          ) : (
            <SettingsView />
          )}
        </main>
      </div>
      <StatusBar onOpenProject={() => openProject()} />
      <OpenProjectDialog open={openDialog} onOpenChange={setOpenDialog} browseFirst={browseFirst} />
      <Toasts />
    </div>
  );
}
