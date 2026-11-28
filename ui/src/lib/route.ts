/** Ruteo por hash (el servidor solo sirve "/"): la vista sobrevive a recargar.
 *
 *  #/factory            tablero
 *  #/factory/CR-04      run abierto
 *  #/code?root=user&path=app/x.py   archivo abierto en el editor
 *  #/settings/providers pestaña de Ajustes
 */
export type View = "code" | "factory" | "settings";

export interface Route {
  view: View;
  run: string | null;
  tab: string | null;
  file: { root: string; path: string } | null;
}

export const DEFAULT_ROUTE: Route = { view: "factory", run: null, tab: null, file: null };

const VIEWS: View[] = ["code", "factory", "settings"];

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [pathPart, query = ""] = raw.split("?", 2);
  const [first, ...rest] = pathPart.split("/").filter(Boolean).map((s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  });
  const view = VIEWS.includes(first as View) ? (first as View) : "factory";
  const route: Route = { ...DEFAULT_ROUTE, view };
  if (view === "factory" && rest[0]) route.run = rest[0];
  if (view === "settings" && rest[0]) route.tab = rest[0];
  if (view === "code") {
    const q = new URLSearchParams(query);
    const root = q.get("root");
    const path = q.get("path");
    if (root && path) route.file = { root, path };
  }
  return route;
}

export function formatRoute(r: Route): string {
  if (r.view === "factory") return r.run ? `#/factory/${encodeURIComponent(r.run)}` : "#/factory";
  if (r.view === "settings") return r.tab ? `#/settings/${encodeURIComponent(r.tab)}` : "#/settings";
  if (r.file) return `#/code?${new URLSearchParams({ root: r.file.root, path: r.file.path }).toString()}`;
  return "#/code";
}
