import { useState } from "react";
import { api, errMsg, type Agent, type ShareLimits } from "../../api";
import { ErrorBox } from "../../components/ui";

type Key = keyof ShareLimits<number>;

export const LIMIT_FIELDS: { key: Key; label: string; hint: string }[] = [
  { key: "session_turns", label: "Messages per conversation", hint: "Then the visitor starts a new conversation." },
  { key: "sessions_per_visitor", label: "Open conversations per visitor", hint: "Conversations close after a day idle." },
  { key: "daily_turns", label: "Messages per day for this link", hint: "Counted per UTC day across all visitors." },
  { key: "concurrent_turns", label: "Messages running at once", hint: "Others wait and try again." },
];

/** Share link limits next to the share link: empty means the server default. */
export default function ShareLimitsEditor({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const overrides = agent.share_limits;
  const defaults = agent.share_limit_defaults;
  const [values, setValues] = useState<Record<Key, string>>(() => {
    const out = {} as Record<Key, string>;
    for (const f of LIMIT_FIELDS) out[f.key] = overrides?.[f.key] != null ? String(overrides[f.key]) : "";
    return out;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function save() {
    const body: Partial<ShareLimits<number | null>> = {};
    for (const f of LIMIT_FIELDS) {
      const raw = values[f.key].trim();
      if (raw === "") body[f.key] = null;
      else {
        const n = Number(raw);
        if (!Number.isInteger(n) || n < 1) {
          setError(`${f.label} must be a whole number of at least 1, or empty for the default.`);
          return;
        }
        body[f.key] = n;
      }
    }
    setBusy(true);
    setError(null);
    try {
      await api.patchAgent(agent.slug, { share_limits: body });
      setSaved(true);
      onChanged();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 border-t border-neutral-200 pt-4 dark:border-neutral-800" data-testid="share-limits">
      <div>
        <h3 className="text-sm font-medium">Limits for this link</h3>
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Visitors who reach a limit see a short message. Monthly budgets and per run caps from Configure apply too.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {LIMIT_FIELDS.map((f) => (
          <label key={f.key} className="block text-sm">
            <span className="label mb-1 block">{f.label}</span>
            <input
              className="input"
              inputMode="numeric"
              aria-label={f.label}
              placeholder={defaults ? `Default ${defaults[f.key]}` : "Default"}
              value={values[f.key]}
              onChange={(e) => {
                setSaved(false);
                setValues((v) => ({ ...v, [f.key]: e.target.value }));
              }}
            />
            <span className="mt-1 block text-xs text-neutral-500">{f.hint}</span>
          </label>
        ))}
      </div>
      <ErrorBox error={error} />
      <div className="flex items-center gap-2">
        <button type="button" className="btn-secondary btn-sm" onClick={save} disabled={busy}>
          {busy ? "Saving..." : "Save limits"}
        </button>
        {saved && <span className="text-xs text-neutral-500">Saved</span>}
      </div>
    </div>
  );
}
