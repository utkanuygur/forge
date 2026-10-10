import { useCallback, useEffect, useState } from "react";
import { KeyRound, RefreshCw, RotateCw } from "lucide-react";
import { errMsg } from "../api";
import SettingField, { fromDraft, toDraft } from "../components/settings/SettingField";
import StatusCards from "../components/settings/StatusCards";
import { useToast } from "../components/Toast";
import { ErrorBox, Loading, Section } from "../components/ui";
import {
  desktopBridge,
  resetPublicUrlCache,
  settingErrors,
  settingsApi,
  type ProviderStatus,
  type SettingsPayload,
  type SettingView,
} from "../settingsApi";

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function draftsFrom(p: SettingsPayload, keep?: Record<string, unknown>, keepKeys?: Set<string>) {
  const out: Record<string, unknown> = {};
  for (const g of p.groups)
    for (const s of g.settings) out[s.key] = keep && keepKeys?.has(s.key) && s.key in keep ? keep[s.key] : toDraft(s);
  return out;
}

function KeysHelp({ payload }: { payload: SettingsPayload }) {
  const bridge = desktopBridge();
  if (bridge)
    return (
      <button type="button" className="btn-secondary btn-sm" onClick={() => bridge.openKeySettings()}>
        <KeyRound className="h-3.5 w-3.5" /> Manage keys
      </button>
    );
  return (
    <span className="text-xs text-neutral-500">
      {payload.desktop ? "Change keys in the Forge app: Forge menu, Settings." : "Set keys in .env, then restart the server."}
    </span>
  );
}

export default function SettingsPage() {
  const toast = useToast();
  const [payload, setPayload] = useState<SettingsPayload | null>(null);
  const [providers, setProviders] = useState<ProviderStatus[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const bridge = desktopBridge();

  const loadProviders = useCallback(async (refresh = false) => {
    try {
      setProviders((await settingsApi.models({ refresh })).providers);
    } catch {
      setProviders([]);
    }
  }, []);

  useEffect(() => {
    settingsApi
      .get()
      .then((p) => {
        setPayload(p);
        setDrafts(draftsFrom(p));
      })
      .catch((e) => setError(errMsg(e)));
    loadProviders();
  }, [loadProviders]);

  function apply(p: SettingsPayload, savedKeys: string[]) {
    setPayload(p);
    // Fresh drafts for what was just saved; unsaved edits elsewhere on the page are kept.
    const saved = new Set(savedKeys);
    const keep = new Set(Object.keys(drafts).filter((k) => !saved.has(k)));
    setDrafts(draftsFrom(p, drafts, keep));
    resetPublicUrlCache();
  }

  const dirtyKeys = (items: SettingView[]) => items.filter((s) => s.editable && !same(drafts[s.key], toDraft(s))).map((s) => s.key);

  async function saveGroup(name: string, items: SettingView[]) {
    const keys = dirtyKeys(items);
    const body: Record<string, unknown> = {};
    const clientErrors: Record<string, string> = {};
    for (const k of keys) {
      const s = items.find((x) => x.key === k)!;
      try {
        body[k] = fromDraft(s, drafts[k]);
      } catch (e) {
        clientErrors[k] = (e as Error).message;
      }
    }
    setErrors((prev) => {
      const next = { ...prev };
      for (const s of items) delete next[s.key];
      return { ...next, ...clientErrors };
    });
    if (Object.keys(clientErrors).length) return;
    setBusy(name);
    try {
      const p = await settingsApi.patch(body);
      apply(p, keys);
      toast(`Saved ${name} settings.`);
      if (keys.includes("openai_endpoints")) loadProviders(true);
    } catch (e) {
      const per = settingErrors(e);
      if (per) setErrors((prev) => ({ ...prev, ...per }));
      else setError(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  async function resetKey(s: SettingView) {
    try {
      apply(await settingsApi.reset([s.key]), [s.key]);
      setErrors((prev) => {
        const next = { ...prev };
        delete next[s.key];
        return next;
      });
      toast(`${s.label} is back to its default.`);
      if (s.key === "openai_endpoints") loadProviders(true);
    } catch (e) {
      setError(errMsg(e));
    }
  }

  async function restart() {
    if (!bridge) return;
    setRestarting(true);
    const r = await bridge.restartServer();
    if (!r.ok) {
      setRestarting(false);
      setError(r.error || "The server did not restart.");
    }
  }

  if (error && !payload) return <ErrorBox error={error} />;
  if (!payload) return <Loading label="Loading settings" />;
  const info = payload.info;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
            Changes apply right away unless marked Needs restart. Values set in the environment are read only here.
          </p>
        </div>
        <button type="button" className="btn-secondary btn-sm" onClick={() => loadProviders(true)}>
          <RefreshCw className="h-3.5 w-3.5" /> Check providers
        </button>
      </div>

      {payload.needs_restart && (
        <div role="status" data-testid="restart-banner" className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <RotateCw className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1">
            Restart required. Some saved settings take effect after the server restarts.
            {!bridge && " Stop the server and start it again to apply them."}
          </span>
          {bridge && (
            <button type="button" className="btn-primary btn-sm" onClick={restart} disabled={restarting}>
              {restarting ? "Restarting..." : "Restart"}
            </button>
          )}
        </div>
      )}

      <StatusCards payload={payload} providers={providers} keysAction={<KeysHelp payload={payload} />} />

      <ErrorBox error={error} onDismiss={() => setError(null)} />

      {payload.groups.map((g) => {
        const dirty = dirtyKeys(g.settings).length > 0;
        return (
          <Section
            key={g.name}
            title={g.name}
            actions={
              g.settings.some((s) => s.editable) && (
                <button type="button" className="btn-primary btn-sm" disabled={!dirty || busy === g.name} onClick={() => saveGroup(g.name, g.settings)} aria-label={`Save ${g.name}`}>
                  {busy === g.name ? "Saving..." : "Save"}
                </button>
              )
            }
          >
            <div className="card divide-y divide-neutral-200 p-4 sm:p-5 dark:divide-neutral-800" data-testid={`group-${g.name}`}>
              {g.settings.map((s) => (
                <SettingField
                  key={s.key}
                  s={s}
                  value={drafts[s.key]}
                  onChange={(v) => setDrafts((d) => ({ ...d, [s.key]: v }))}
                  onReset={() => resetKey(s)}
                  error={errors[s.key]}
                  providers={providers}
                  keysAction={<KeysHelp payload={payload} />}
                />
              ))}
            </div>
          </Section>
        );
      })}

      <Section title="About this server">
        <dl className="card grid grid-cols-1 gap-x-6 gap-y-3 p-4 text-sm sm:grid-cols-[auto_1fr] sm:p-5">
          <dt className="text-neutral-500">Version</dt>
          <dd className="font-mono text-xs" data-testid="info-version">{info.version}</dd>
          <dt className="text-neutral-500">Data directory</dt>
          <dd className="break-all font-mono text-xs" data-testid="info-data-dir">{info.data_dir}</dd>
          <dt className="text-neutral-500">Executor</dt>
          <dd className="font-mono text-xs">{info.executor}</dd>
          <dt className="text-neutral-500">Running in</dt>
          <dd>{payload.desktop ? "The Forge desktop app" : "A development server"}</dd>
        </dl>
      </Section>
    </div>
  );
}
