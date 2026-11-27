import * as React from "react";
import { api, errorMessage, type Meta, type Profile, type SessionInfo } from "./api";

export type View = "code" | "factory" | "settings";
export type Theme = "dark" | "light";

interface Toast {
  id: number;
  tone: "info" | "success" | "danger";
  text: string;
}

export interface Signer {
  profile: Profile | null;
  role: string;
}

interface AppState {
  session: SessionInfo | null;
  meta: Meta | null;
  loadError: string | null;
  refreshSession: () => Promise<void>;
  view: View;
  setView: (v: View) => void;
  theme: Theme;
  toggleTheme: () => void;
  signer: Signer;
  setSigner: (profileId: string | null, role?: string) => void;
  toasts: Toast[];
  notify: (text: string, tone?: Toast["tone"]) => void;
  /** Cambia cada vez que el backend reporta un cambio (SSE). */
  tick: number;
  live: boolean;
  /** Run abierto en el tablero (compartido con el selector de raiz del editor). */
  selectedRun: string | null;
  setSelectedRun: (id: string | null) => void;
  openFile: (root: string, path: string) => void;
  pendingOpen: { root: string; path: string } | null;
  clearPendingOpen: () => void;
}

const Ctx = React.createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = React.useContext(Ctx);
  if (!ctx) throw new Error("useApp fuera de AppProvider");
  return ctx;
}

const LS_SIGNER = "factory.signer";
const LS_THEME = "factory.theme";

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = React.useState<SessionInfo | null>(null);
  const [meta, setMeta] = React.useState<Meta | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [view, setView] = React.useState<View>("factory");
  const [theme, setTheme] = React.useState<Theme>(() =>
    readJson<Theme>(LS_THEME, window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark"),
  );
  const [signerRef, setSignerRef] = React.useState(() =>
    readJson<{ id: string | null; role: string }>(LS_SIGNER, { id: null, role: "" }),
  );
  const [toasts, setToasts] = React.useState<Toast[]>([]);
  const [tick, setTick] = React.useState(0);
  const [live, setLive] = React.useState(false);
  const [selectedRun, setSelectedRun] = React.useState<string | null>(null);
  const [pendingOpen, setPendingOpen] = React.useState<{ root: string; path: string } | null>(null);

  const notify = React.useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, tone, text }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "danger" ? 7000 : 3500);
  }, []);

  const refreshSession = React.useCallback(async () => {
    try {
      setSession(await api.session());
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  React.useEffect(() => {
    refreshSession();
    api.meta().then(setMeta).catch((err) => setLoadError(errorMessage(err)));
  }, [refreshSession]);

  React.useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem(LS_THEME, JSON.stringify(theme));
  }, [theme]);

  // Eventos en vivo: el backend emite un snapshot cuando cambia un estado de run o los CRs.
  React.useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const source = new EventSource("/api/events");
    source.addEventListener("change", () => setTick((t) => t + 1));
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false);
    return () => source.close();
  }, []);

  const profiles = session?.profiles ?? [];
  const profile = profiles.find((p) => p.id === signerRef.id) ?? null;
  const role = profile && profile.roles.includes(signerRef.role) ? signerRef.role : profile?.roles[0] ?? "";

  const setSigner = React.useCallback((id: string | null, nextRole?: string) => {
    const value = { id, role: nextRole ?? "" };
    setSignerRef(value);
    localStorage.setItem(LS_SIGNER, JSON.stringify(value));
  }, []);

  const value: AppState = {
    session,
    meta,
    loadError,
    refreshSession,
    view,
    setView,
    theme,
    toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    signer: { profile, role },
    setSigner,
    toasts,
    notify,
    tick,
    live,
    selectedRun,
    setSelectedRun,
    openFile: (root, path) => {
      setPendingOpen({ root, path });
      setView("code");
    },
    pendingOpen,
    clearPendingOpen: () => setPendingOpen(null),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Carga asincrona que se repite cuando cambian las dependencias (p. ej. `tick`). */
export function useLoad<T>(load: () => Promise<T>, deps: React.DependencyList) {
  const [data, setData] = React.useState<T | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);
  const counter = React.useRef(0);
  const reload = React.useCallback(async () => {
    const n = ++counter.current;
    setLoading(true);
    try {
      const result = await load();
      if (n === counter.current) {
        setData(result);
        setError(null);
      }
    } catch (err) {
      if (n === counter.current) setError(errorMessage(err));
    } finally {
      if (n === counter.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  React.useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}
