import { Bot, FolderTree, GitBranch, Pencil, Plus, Save, ShieldCheck, Trash2, Undo2, Users } from "lucide-react";
import * as React from "react";
import {
  Badge,
  Button,
  Callout,
  Card,
  CardHeader,
  Dialog,
  DialogContent,
  Field,
  Input,
  ListInput,
  Select,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui";
import { api, errorMessage, type Profile, type ProjectConfig } from "@/lib/api";
import { useApp } from "@/lib/app-state";
import { VerifyEditor } from "./VerifyEditor";

const list = (v: string) =>
  v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

const ROLE_LABEL: Record<string, string> = {
  intake: "Intake",
  planner: "Planner",
  coder: "Coder",
  test_analyst: "Test Analyst",
  reviewer: "Reviewer",
  security: "Security",
  evidence: "Evidence",
};

const GATE_LABEL: Record<string, string> = {
  requirements: "Gate requisitos",
  plan: "Gate plan",
  review: "Gate review",
  findings: "Clasificar findings",
  release: "Gate release",
};

export function SettingsView() {
  const { session, meta, refreshSession, notify } = useApp();
  const saved = session?.project?.config ?? null;
  const [draft, setDraft] = React.useState<ProjectConfig | null>(saved);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => setDraft(saved), [saved]);

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const patch = (fn: (c: ProjectConfig) => void) =>
    setDraft((c) => {
      if (!c) return c;
      const next = structuredClone(c);
      fn(next);
      return next;
    });

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      await api.saveConfig(draft);
      await refreshSession();
      notify("Ajustes guardados en .factory/config.toml", "success");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-6 py-3">
        <div>
          <h1 className="text-base font-semibold">Ajustes</h1>
          <p className="text-xs text-muted-foreground">
            {session?.project ? (
              <>
                Configuración del flujo en <code>.factory/config.toml</code>. El orden de fases y
                gates es fijo.
              </>
            ) : (
              "Abre un proyecto para configurar su flujo."
            )}
          </p>
        </div>
        {draft ? (
          <div className="flex items-center gap-2">
            {dirty ? <Badge tone="warning">cambios sin guardar</Badge> : null}
            <Button variant="ghost" size="sm" disabled={!dirty} onClick={() => setDraft(saved)}>
              <Undo2 /> Descartar
            </Button>
            <Button size="sm" disabled={!dirty} loading={busy} onClick={save}>
              <Save /> Guardar
            </Button>
          </div>
        ) : null}
      </header>
      <Tabs defaultValue={draft ? "agents" : "profiles"} className="flex min-h-0 flex-1 flex-col">
        <TabsList className="px-6">
          {draft ? (
            <>
              <TabsTrigger value="agents"><Bot /> Agentes y límites</TabsTrigger>
              <TabsTrigger value="verify"><ShieldCheck /> Verificación</TabsTrigger>
              <TabsTrigger value="paths"><FolderTree /> Rutas y código</TabsTrigger>
              <TabsTrigger value="gates"><GitBranch /> Gates y git</TabsTrigger>
            </>
          ) : null}
          <TabsTrigger value="profiles"><Users /> Perfiles</TabsTrigger>
        </TabsList>
        <div className="min-h-0 flex-1 overflow-auto px-6 py-5">
          <div className="mx-auto max-w-3xl space-y-4">
            {error ? <Callout tone="danger" title="No se guardó">{error}</Callout> : null}
            {draft ? (
              <>
                <TabsContent value="agents" className="space-y-4">
                  <Card>
                    <CardHeader title="Modelo por defecto" description="Lo usan todos los agentes salvo que se cambie por rol. FACTORY_MODEL (entorno) tiene prioridad." />
                    <div className="grid grid-cols-2 gap-3 p-4">
                      <Field label="Modelo">
                        <Input value={draft.agents.model} onChange={(e) => patch((c) => (c.agents.model = e.target.value))} />
                      </Field>
                      <Field label={`Temperatura (${draft.agents.temperature})`}>
                        <input
                          type="range"
                          min={0}
                          max={1}
                          step={0.05}
                          aria-label="Temperatura por defecto"
                          value={draft.agents.temperature}
                          onChange={(e) => patch((c) => (c.agents.temperature = Number(e.target.value)))}
                          className="mt-2 accent-[var(--primary)]"
                        />
                      </Field>
                    </div>
                  </Card>
                  <Card>
                    <CardHeader title="Por agente" description="Vacío = usa el valor por defecto." />
                    <div className="divide-y divide-border">
                      {(meta?.agent_roles ?? Object.keys(ROLE_LABEL)).map((role) => {
                        const o = draft.agents.roles[role] ?? {};
                        const set = (p: { model?: string | null; temperature?: number | null }) =>
                          patch((c) => {
                            const next = { ...(c.agents.roles[role] ?? {}), ...p };
                            if (!next.model && (next.temperature === null || next.temperature === undefined)) delete c.agents.roles[role];
                            else c.agents.roles[role] = next;
                          });
                        return (
                          <div key={role} className="grid grid-cols-[9rem_1fr_8rem] items-center gap-3 px-4 py-2">
                            <span className="text-sm">{ROLE_LABEL[role] ?? role}</span>
                            <Input aria-label={`Modelo ${role}`} placeholder={draft.agents.model} value={o.model ?? ""} onChange={(e) => set({ model: e.target.value || null })} />
                            <Input
                              aria-label={`Temperatura ${role}`}
                              type="number"
                              min={0}
                              max={2}
                              step={0.05}
                              placeholder={String(draft.agents.temperature)}
                              value={o.temperature ?? ""}
                              onChange={(e) => set({ temperature: e.target.value === "" ? null : Number(e.target.value) })}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </Card>
                  <Card>
                    <CardHeader title="Límites de rondas" description="Al agotarse, el run se escala a un humano (nunca se aprueba solo)." />
                    <div className="grid grid-cols-2 gap-3 p-4">
                      <Field label="Rondas de verificación → Coder Fix">
                        <Input type="number" min={1} max={10} value={draft.limits.verify_rounds} onChange={(e) => patch((c) => (c.limits.verify_rounds = Number(e.target.value)))} />
                      </Field>
                      <Field label="Rondas de review → Coder Fix">
                        <Input type="number" min={1} max={10} value={draft.limits.review_rounds} onChange={(e) => patch((c) => (c.limits.review_rounds = Number(e.target.value)))} />
                      </Field>
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="verify" className="space-y-4">
                  <Card>
                    <CardHeader title="Comandos de verificación" description="Se ejecutan en el worktree del run, en el entorno del proyecto, sin shell." />
                    <div className="space-y-4 p-4">
                      <VerifyEditor commands={draft.verify.commands} onChange={(cmds) => patch((c) => (c.verify.commands = cmds))} />
                      <Field label="Timeout por comando (s)" className="max-w-48">
                        <Input type="number" min={5} max={3600} value={draft.verify.timeout} onChange={(e) => patch((c) => (c.verify.timeout = Number(e.target.value)))} />
                      </Field>
                    </div>
                  </Card>
                  <Card>
                    <CardHeader title="Escáneres de seguridad" description="Semgrep solo escanea las rutas que existan." />
                    <div className="p-4">
                      <Field label="Objetivos de semgrep (vacío = código fuente)">
                        <ListInput value={draft.scanners.semgrep_targets} onChange={(v) => patch((c) => (c.scanners.semgrep_targets = v))} />
                      </Field>
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="paths" className="space-y-4">
                  <Card>
                    <CardHeader title="Código" description="Solo estas áreas son escribibles por los agentes; .git, .factory y .env nunca." />
                    <div className="grid grid-cols-2 gap-3 p-4">
                      <Field label="Lenguaje">
                        <Select value={draft.code.language} onChange={(e) => patch((c) => (c.code.language = e.target.value))}>
                          {(meta?.languages ?? ["python", "javascript", "typescript", "other"]).map((l) => (
                            <option key={l}>{l}</option>
                          ))}
                        </Select>
                      </Field>
                      <Field label="Tests (escribible por Test Analyst)">
                        <Input value={draft.code.tests} onChange={(e) => patch((c) => (c.code.tests = e.target.value))} />
                      </Field>
                      <Field label="Código fuente (escribible por Coder)">
                        <ListInput value={draft.code.source} onChange={(v) => patch((c) => (c.code.source = v))} />
                      </Field>
                      <Field label="Solo lectura adicional">
                        <ListInput value={draft.code.readable} onChange={(v) => patch((c) => (c.code.readable = v))} />
                      </Field>
                      <Field label="Nunca escribir" className="col-span-2">
                        <ListInput value={draft.code.never_write} onChange={(v) => patch((c) => (c.code.never_write = v))} />
                      </Field>
                    </div>
                  </Card>
                  <Card>
                    <CardHeader title="Documentos de contexto" description="Fuente de verdad que leen los agentes." />
                    <div className="grid grid-cols-2 gap-3 p-4">
                      {(["requirements", "spec", "architecture", "repo_map"] as const).map((k) => (
                        <Field key={k} label={k}>
                          <Input value={draft.docs[k] ?? ""} onChange={(e) => patch((c) => (c.docs[k] = e.target.value || null))} />
                        </Field>
                      ))}
                    </div>
                  </Card>
                  <Card>
                    <CardHeader title="Carpetas de la fábrica" />
                    <div className="grid grid-cols-2 gap-3 p-4">
                      {(["change_requests", "runs", "worktrees", "evidence"] as const).map((k) => (
                        <Field key={k} label={k}>
                          <Input value={draft.paths[k]} onChange={(e) => patch((c) => (c.paths[k] = e.target.value))} />
                        </Field>
                      ))}
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="gates" className="space-y-4">
                  <Card>
                    <CardHeader title="Roles autorizados por gate" description="Quien firma debe tener uno de estos roles en su perfil." />
                    <div className="divide-y divide-border">
                      {(meta?.gate_keys ?? Object.keys(GATE_LABEL)).map((g) => (
                        <div key={g} className="grid grid-cols-[10rem_1fr] items-center gap-3 px-4 py-2">
                          <span className="text-sm">{GATE_LABEL[g] ?? g}</span>
                          <ListInput aria-label={`Roles ${g}`} value={draft.gates.roles[g] ?? []} onChange={(v) => patch((c) => (c.gates.roles[g] = v))} />
                        </div>
                      ))}
                    </div>
                  </Card>
                  <Card>
                    <CardHeader title="Git" description="Cada run trabaja en su worktree y rama propia; nunca se hace merge automático." />
                    <div className="grid grid-cols-2 gap-3 p-4">
                      <Field label="Rama base">
                        <Input value={draft.git.base_branch} onChange={(e) => patch((c) => (c.git.base_branch = e.target.value))} />
                      </Field>
                      <Field label="Prefijo de rama" hint="Debe terminar en /">
                        <Input value={draft.git.branch_prefix} onChange={(e) => patch((c) => (c.git.branch_prefix = e.target.value))} />
                      </Field>
                    </div>
                  </Card>
                </TabsContent>
              </>
            ) : null}
            <TabsContent value="profiles">
              <ProfilesPanel />
            </TabsContent>
          </div>
        </div>
      </Tabs>
    </div>
  );
}

function ProfilesPanel() {
  const { session, refreshSession, notify, signer, setSigner } = useApp();
  const profiles = session?.profiles ?? [];
  const gateRoles = session?.project?.config.gates.roles ?? {};
  const knownRoles = Array.from(new Set(Object.values(gateRoles).flat()));
  const [editing, setEditing] = React.useState<Profile | "new" | null>(null);

  const remove = async (p: Profile) => {
    try {
      await api.deleteProfile(p.id);
      if (signer.profile?.id === p.id) setSigner(null);
      await refreshSession();
    } catch (err) {
      notify(errorMessage(err), "danger");
    }
  };

  return (
    <Card>
      <CardHeader
        title="Perfiles locales"
        description="Identifican quién firma cada decisión como «Nombre (Rol)». No son autenticación; se guardan en ~/.agentic-factory."
        actions={
          <Button size="sm" onClick={() => setEditing("new")}>
            <Plus /> Perfil
          </Button>
        }
      />
      {profiles.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">
          Aún no hay perfiles. Crea uno con los roles que firmarás.
        </p>
      ) : (
        <div className="divide-y divide-border">
          {profiles.map((p) => (
            <div key={p.id} className="flex items-center gap-3 px-4 py-2.5">
              <div className="flex size-8 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
                {p.name.slice(0, 2).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">
                  {p.name}
                  {signer.profile?.id === p.id ? <Badge tone="success" className="ml-2">activo</Badge> : null}
                </p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {p.roles.map((r) => (
                    <Badge key={r}>{r}</Badge>
                  ))}
                </div>
              </div>
              {signer.profile?.id !== p.id ? (
                <Button variant="outline" size="xs" onClick={() => setSigner(p.id)}>
                  Usar
                </Button>
              ) : null}
              <Button variant="ghost" size="icon" aria-label={`Editar ${p.name}`} onClick={() => setEditing(p)}>
                <Pencil />
              </Button>
              <Button variant="ghost" size="icon" aria-label={`Borrar ${p.name}`} onClick={() => remove(p)}>
                <Trash2 />
              </Button>
            </div>
          ))}
        </div>
      )}
      <ProfileDialog
        key={editing === "new" ? "new" : editing?.id ?? "none"}
        profile={editing}
        knownRoles={knownRoles}
        onClose={() => setEditing(null)}
      />
    </Card>
  );
}

function ProfileDialog({
  profile,
  knownRoles,
  onClose,
}: {
  profile: Profile | "new" | null;
  knownRoles: string[];
  onClose: () => void;
}) {
  const { refreshSession, setSigner, signer } = useApp();
  const existing = profile && profile !== "new" ? profile : null;
  const [name, setName] = React.useState(existing?.name ?? "");
  const [roles, setRoles] = React.useState<string[]>(existing?.roles ?? []);
  const [extra, setExtra] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const options = Array.from(new Set([...knownRoles, ...roles]));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const all = Array.from(new Set([...roles, ...list(extra)]));
    setBusy(true);
    setError(null);
    try {
      const saved = existing
        ? await api.updateProfile(existing.id, name, all)
        : await api.createProfile(name, all);
      if (!signer.profile) setSigner(saved.id);
      await refreshSession();
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={profile !== null} onOpenChange={(v) => (!v ? onClose() : null)}>
      <DialogContent title={existing ? "Editar perfil" : "Nuevo perfil"} description="El nombre y el rol forman la firma de cada decisión.">
        <form className="space-y-4" onSubmit={submit}>
          <Field label="Nombre">
            <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Ana López" />
          </Field>
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground">Roles (de los gates del proyecto)</p>
            <div className="flex flex-wrap gap-1.5">
              {options.map((r) => {
                const on = roles.includes(r);
                return (
                  <button
                    key={r}
                    type="button"
                    aria-pressed={on}
                    onClick={() => setRoles(on ? roles.filter((x) => x !== r) : [...roles, r])}
                    className={
                      "rounded-full border px-2.5 py-1 text-xs transition-colors " +
                      (on ? "border-primary bg-primary/15 text-primary" : "border-border hover:bg-accent")
                    }
                  >
                    {r}
                  </button>
                );
              })}
            </div>
          </div>
          <Field label="Otros roles (separados por comas)">
            <Input value={extra} onChange={(e) => setExtra(e.target.value)} />
          </Field>
          {error ? <Callout tone="danger">{error}</Callout> : null}
          <div className="flex justify-end">
            <Button type="submit" loading={busy} disabled={!name.trim() || (!roles.length && !extra.trim())}>
              <Save /> Guardar
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
