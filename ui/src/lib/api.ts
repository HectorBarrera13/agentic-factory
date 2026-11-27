// Cliente de la API local de Factory Studio. Autenticacion: cookie HttpOnly (build servido
// por FastAPI) o header X-Factory-Token que agrega el proxy de Vite en desarrollo.

export type Json = Record<string, unknown>;

export interface Profile {
  id: string;
  name: string;
  roles: string[];
}

export interface VerifyCommand {
  name: string;
  cmd: string[] | string;
  kind: string;
}

export interface ProjectConfig {
  paths: { change_requests: string; runs: string; worktrees: string; evidence: string };
  code: {
    source: string[];
    tests: string;
    readable: string[];
    never_write: string[];
    language: string;
  };
  docs: {
    requirements: string | null;
    spec: string | null;
    architecture: string | null;
    repo_map: string | null;
    always_include: string[];
  };
  verify: { commands: VerifyCommand[]; timeout: number };
  git: { base_branch: string; branch_prefix: string };
  agents: {
    model: string;
    temperature: number;
    roles: Record<string, { model?: string | null; temperature?: number | null }>;
  };
  limits: { verify_rounds: number; review_rounds: number };
  gates: { roles: Record<string, string[]> };
  scanners: { semgrep_targets: string[] };
  ids: { acceptance: string; requirement: string };
}

export interface ProjectInfo {
  root: string;
  name: string;
  configured: boolean;
  git: boolean;
  config: ProjectConfig;
}

export interface SessionInfo {
  project: ProjectInfo | null;
  recent: string[];
  profiles: Profile[];
}

export interface Phase {
  id: string;
  label: string;
  artifact?: string;
  gate?: string;
}

export interface Meta {
  phases: Phase[];
  gates: string[];
  decisions: string[];
  verdicts: string[];
  agent_roles: string[];
  gate_keys: string[];
  verify_kinds: string[];
  languages: string[];
  defaults: ProjectConfig;
}

export interface TreeEntry {
  name: string;
  path: string;
  type: "dir" | "file";
  secret?: boolean;
  children?: TreeEntry[];
}

export interface FileTree {
  root: string;
  readonly: boolean;
  truncated: boolean;
  entries: TreeEntry[];
}

export interface FileContent {
  path: string;
  readonly: boolean;
  secret: boolean;
  binary: boolean;
  content: string;
}

export interface ChangeRequest {
  change_id: string;
  file: string;
  title: string;
  has_run: boolean;
}

export interface RunSummary {
  change_id: string;
  status: string;
  awaiting_gate: string;
  stop_reason: string;
  created_at?: string;
  busy?: boolean;
}

export interface Outcome {
  kind: "running" | "done" | "awaiting" | "blocked" | "transient" | "error";
  status?: string;
  gate?: string;
  message?: string;
  mode?: string;
  at: number;
}

export interface Finding {
  id: string;
  severity: string;
  blocking: boolean;
  decision: string | null;
  excerpt: string;
}

export interface Artifact {
  name: string;
  size: number;
  modified: number;
}

export interface RunState {
  change_id: string;
  status: string;
  awaiting_gate?: string;
  stop_reason?: string;
  human_gates: Record<string, string>;
  verification?: { status: string; tests: number; failed: number; lint: string };
  review?: { status: string; must_fix: number; should_fix: number };
  security?: { status: string; high: number; medium: number };
  iterations?: number;
  branch?: string;
  worktree?: string;
  [key: string]: unknown;
}

export interface RunDetail {
  change_id: string;
  state: RunState;
  busy: boolean;
  outcome: Outcome | null;
  artifacts: Artifact[];
  gate: {
    name: string;
    question: string;
    artifacts: string[];
    roles: string[];
    findings_roles: string[];
    /** Decision ya registrada; el motor la aplica en el siguiente "Avanzar". */
    recorded: { decision: string; reason: string; recorded_by: string; recorded_at: string } | null;
  } | null;
  findings: { items: Finding[]; pending: string[] };
  worktree: string | null;
}

export interface DiffFiles {
  base: string;
  branch: string;
  files: { status: string; path: string }[];
}

export interface Detected {
  config: ProjectConfig;
  notes: string[];
  has_config: boolean;
}

export class ApiError extends Error {
  status: number;
  details?: unknown;
  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const obj = (data ?? {}) as { error?: string; detail?: unknown; details?: unknown };
    let message = obj.error ?? `Error ${res.status}`;
    if (!obj.error && Array.isArray(obj.detail)) message = "Datos invalidos";
    throw new ApiError(message, res.status, obj.details ?? obj.detail);
  }
  return data as T;
}

const q = (params: Record<string, string>) => new URLSearchParams(params).toString();
const enc = encodeURIComponent;

export const api = {
  session: () => request<SessionInfo>("GET", "/api/session"),
  meta: () => request<Meta>("GET", "/api/meta"),
  openProject: (path: string) => request<ProjectInfo>("POST", "/api/projects/open", { path }),
  detect: (path: string) => request<Detected>("POST", "/api/projects/detect", { path }),
  saveConfig: (config: ProjectConfig) => request<ProjectInfo>("PUT", "/api/config", config),

  tree: (root: string) => request<FileTree>("GET", `/api/files/tree?${q({ root })}`),
  readFile: (root: string, path: string) =>
    request<FileContent>("GET", `/api/files/content?${q({ root, path })}`),
  writeFile: (root: string, path: string, content: string) =>
    request<{ path: string; bytes: number }>("PUT", `/api/files/content?${q({ root })}`, {
      path,
      content,
    }),
  gitStatus: () => request<{ files: { status: string; path: string }[] }>("GET", "/api/git/status"),

  crs: () => request<{ items: ChangeRequest[] }>("GET", "/api/crs"),
  cr: (id: string) =>
    request<{ change_id: string; file: string; body: string; frozen: boolean }>(
      "GET",
      `/api/crs/${enc(id)}`,
    ),
  createCr: (change_id: string, title: string, body: string) =>
    request<{ file: string }>("POST", "/api/crs", { change_id, title, body }),
  updateCr: (id: string, body: string) =>
    request<{ file: string }>("PUT", `/api/crs/${enc(id)}`, { body }),

  runs: () => request<{ items: RunSummary[] }>("GET", "/api/runs"),
  startRun: (change_id: string) => request<RunState>("POST", "/api/runs", { change_id }),
  run: (id: string) => request<RunDetail>("GET", `/api/runs/${enc(id)}`),
  artifact: (id: string, name: string) =>
    request<{ name: string; content: string }>(
      "GET",
      `/api/runs/${enc(id)}/artifacts/${enc(name)}`,
    ),
  advance: (id: string, mode: "fake" | "adk") =>
    request<Outcome>("POST", `/api/runs/${enc(id)}/advance`, { mode }),
  decideGate: (id: string, gate: string, body: Json) =>
    request<Json>("POST", `/api/runs/${enc(id)}/gates/${enc(gate)}`, body),
  decideFindings: (id: string, body: Json) =>
    request<{ decisions: unknown[]; pending: string[] }>(
      "POST",
      `/api/runs/${enc(id)}/findings`,
      body,
    ),
  diff: (id: string) => request<DiffFiles>("GET", `/api/runs/${enc(id)}/diff`),
  diffFile: (id: string, path: string) =>
    request<{ path: string; original: string; modified: string }>(
      "GET",
      `/api/runs/${enc(id)}/diff/file?${q({ path })}`,
    ),
  cleanup: (id: string) => request<{ removed: boolean }>("POST", `/api/runs/${enc(id)}/cleanup`),
  exportEvidence: (id: string) =>
    request<{ files: string[]; dest: string }>("POST", `/api/runs/${enc(id)}/export`),

  profiles: () => request<{ items: Profile[] }>("GET", "/api/profiles"),
  createProfile: (name: string, roles: string[]) =>
    request<Profile>("POST", "/api/profiles", { name, roles }),
  updateProfile: (id: string, name: string, roles: string[]) =>
    request<Profile>("PUT", `/api/profiles/${enc(id)}`, { name, roles }),
  deleteProfile: (id: string) => request<{ deleted: boolean }>("DELETE", `/api/profiles/${enc(id)}`),
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (Array.isArray(err.details) && err.details.length) {
      const items = err.details as { loc?: unknown[]; msg?: string }[];
      return `${err.message}: ${items
        .map((d) => `${(d.loc ?? []).join(".")} ${d.msg ?? ""}`.trim())
        .join("; ")}`;
    }
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}
