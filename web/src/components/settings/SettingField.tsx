import { Plus, RotateCcw, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import type { Endpoint, ProviderStatus, SettingView } from "../../settingsApi";
import { Badge } from "../ui";
import ModelPicker from "./ModelPicker";

export interface PriceRow {
  model: string;
  input: string;
  output: string;
}

/** The editor value for a setting (numbers as text, prices as rows) from its API value. */
export function toDraft(s: SettingView): unknown {
  const v = s.value;
  if (s.type === "int" || s.type === "float") return v === null || v === undefined ? "" : String(v);
  if (s.widget === "prices") {
    return Object.entries((v as Record<string, [number, number]>) || {}).map(([model, [i, o]]) => ({
      model,
      input: String(i),
      output: String(o),
    }));
  }
  if (s.widget === "endpoints") return ((v as Endpoint[]) || []).map((e) => ({ ...e }));
  if (s.type === "json") return JSON.stringify(v ?? null, null, 2);
  return v ?? "";
}

/** The API value for an editor value; throws Error with a plain message when it cannot be converted. */
export function fromDraft(s: SettingView, d: unknown): unknown {
  if (s.widget === "prices") {
    const out: Record<string, [number, number]> = {};
    for (const r of d as PriceRow[]) {
      if (!r.model.trim()) throw new Error("every price needs a model");
      const i = Number(r.input);
      const o = Number(r.output);
      if (r.input.trim() === "" || r.output.trim() === "" || !Number.isFinite(i) || !Number.isFinite(o))
        throw new Error(`prices for ${r.model} must be numbers`);
      out[r.model.trim()] = [i, o];
    }
    return out;
  }
  if (s.widget === "endpoints") return d;
  if (s.type === "json") {
    try {
      return JSON.parse(String(d));
    } catch {
      throw new Error("must be valid JSON");
    }
  }
  if (s.type === "int" || s.type === "float") {
    const t = String(d).trim();
    const n = Number(t);
    if (t === "" || !Number.isFinite(n)) throw new Error("must be a number");
    if (s.type === "int" && !Number.isInteger(n)) throw new Error("must be a whole number");
    if (s.min !== undefined && n < s.min) throw new Error(`must be at least ${s.min}`);
    if (s.max !== undefined && n > s.max) throw new Error(`must be at most ${s.max}`);
    return n;
  }
  return d;
}

const SOURCE: Record<string, { label: string; tone: "neutral" | "accent" | "amber" }> = {
  env: { label: "Environment", tone: "amber" },
  saved: { label: "Saved", tone: "accent" },
  default: { label: "Default", tone: "neutral" },
};

export function SourceBadge({ s }: { s: SettingView }) {
  const src = SOURCE[s.source];
  return (
    <span data-testid={`source-${s.key}`} data-source={s.source}>
      <Badge tone={src.tone}>{src.label}</Badge>
    </span>
  );
}

function EndpointsEditor({ id, value, onChange, disabled }: { id: string; value: Endpoint[]; onChange: (v: Endpoint[]) => void; disabled: boolean }) {
  const set = (i: number, patch: Partial<Endpoint>) => onChange(value.map((e, j) => (j === i ? { ...e, ...patch } : e)));
  return (
    <div className="space-y-2" id={id}>
      {value.length === 0 && <p className="text-xs text-neutral-500">No endpoints. Add Ollama, vLLM or another OpenAI compatible server.</p>}
      {value.map((e, i) => (
        <div key={i} className="grid grid-cols-1 gap-2 rounded-lg border border-neutral-200 p-2 sm:grid-cols-[1fr_2fr_1.3fr_auto] dark:border-neutral-800" data-testid="endpoint-row">
          <input className="input text-xs" aria-label={`Endpoint ${i + 1} name`} placeholder="Name" value={e.name} disabled={disabled} onChange={(ev) => set(i, { name: ev.target.value })} />
          <input className="input font-mono text-xs" aria-label={`Endpoint ${i + 1} base URL`} placeholder="http://localhost:11434/v1" value={e.base_url} disabled={disabled} onChange={(ev) => set(i, { base_url: ev.target.value })} />
          <input
            className="input font-mono text-xs"
            aria-label={`Endpoint ${i + 1} API key variable`}
            placeholder="Key variable (optional)"
            value={e.api_key_env || ""}
            disabled={disabled}
            onChange={(ev) => set(i, { api_key_env: ev.target.value.toUpperCase() || undefined })}
          />
          <button type="button" className="btn-ghost btn-sm justify-self-end" aria-label={`Remove endpoint ${e.name || i + 1}`} disabled={disabled} onClick={() => onChange(value.filter((_, j) => j !== i))}>
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      {!disabled && (
        <button type="button" className="btn-secondary btn-sm" onClick={() => onChange([...value, { name: "", base_url: "" }])}>
          <Plus className="h-3.5 w-3.5" /> Add endpoint
        </button>
      )}
    </div>
  );
}

function PricesEditor({ id, value, onChange, disabled }: { id: string; value: PriceRow[]; onChange: (v: PriceRow[]) => void; disabled: boolean }) {
  const set = (i: number, patch: Partial<PriceRow>) => onChange(value.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="space-y-2" id={id}>
      {value.length === 0 ? (
        <p className="text-xs text-neutral-500">No extra prices. Built in Claude prices apply; self hosted models cost nothing.</p>
      ) : (
        <div className="hidden grid-cols-[2fr_1fr_1fr_auto] gap-2 px-2 text-xs text-neutral-500 sm:grid">
          <span>Model</span>
          <span>Input USD per million</span>
          <span>Output USD per million</span>
          <span className="w-8" />
        </div>
      )}
      {value.map((r, i) => (
        <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border border-neutral-200 p-2 sm:grid-cols-[2fr_1fr_1fr_auto] dark:border-neutral-800" data-testid="price-row">
          <input className="input col-span-2 font-mono text-xs sm:col-span-1" aria-label={`Price ${i + 1} model`} placeholder="claude-x or fake:hello" value={r.model} disabled={disabled} onChange={(e) => set(i, { model: e.target.value })} />
          <input className="input text-xs" inputMode="decimal" aria-label={`Price ${i + 1} input`} placeholder="Input" value={r.input} disabled={disabled} onChange={(e) => set(i, { input: e.target.value })} />
          <input className="input text-xs" inputMode="decimal" aria-label={`Price ${i + 1} output`} placeholder="Output" value={r.output} disabled={disabled} onChange={(e) => set(i, { output: e.target.value })} />
          <button type="button" className="btn-ghost btn-sm col-span-2 justify-self-end sm:col-span-1" aria-label={`Remove price ${r.model || i + 1}`} disabled={disabled} onClick={() => onChange(value.filter((_, j) => j !== i))}>
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      {!disabled && (
        <button type="button" className="btn-secondary btn-sm" onClick={() => onChange([...value, { model: "", input: "", output: "" }])}>
          <Plus className="h-3.5 w-3.5" /> Add price
        </button>
      )}
    </div>
  );
}

function Control({ s, value, onChange, providers }: { s: SettingView; value: unknown; onChange: (v: unknown) => void; providers: ProviderStatus[] | null }) {
  const id = `setting-${s.key}`;
  const disabled = !s.editable;
  if (s.type === "model")
    return <ModelPicker id={id} label={s.label} value={String(value ?? "")} onChange={onChange} providers={providers} disabled={disabled} placeholder={s.placeholder} />;
  if (s.type === "bool")
    return (
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={Boolean(value)}
        aria-label={s.label}
        disabled={disabled}
        onClick={() => onChange(!value)}
        className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition disabled:opacity-60 ${value ? "bg-accent-600" : "bg-neutral-300 dark:bg-neutral-700"}`}
      >
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition ${value ? "left-[18px]" : "left-0.5"}`} />
      </button>
    );
  if (s.type === "enum")
    return (
      <select id={id} className="input sm:max-w-xs" value={String(value)} disabled={disabled} aria-label={s.label} onChange={(e) => onChange(e.target.value)}>
        {(s.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  if (s.widget === "endpoints") return <EndpointsEditor id={id} value={value as Endpoint[]} onChange={onChange} disabled={disabled} />;
  if (s.widget === "prices") return <PricesEditor id={id} value={value as PriceRow[]} onChange={onChange} disabled={disabled} />;
  if (s.type === "json")
    return <textarea id={id} className="input font-mono text-xs" rows={4} value={String(value)} disabled={disabled} aria-label={s.label} onChange={(e) => onChange(e.target.value)} />;
  const numeric = s.type === "int" || s.type === "float";
  return (
    <input
      id={id}
      className={`input ${numeric ? "sm:max-w-[10rem]" : s.type === "url" || s.key.includes("dir") ? "font-mono text-xs" : ""}`}
      inputMode={numeric ? (s.type === "int" ? "numeric" : "decimal") : undefined}
      value={String(value ?? "")}
      disabled={disabled}
      aria-label={s.label}
      placeholder={s.placeholder}
      spellCheck={false}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function SecretStatus({ s, keysAction }: { s: SettingView; keysAction: ReactNode }) {
  const where = s.from_desktop ? "Set in the Forge app (Keychain)" : s.is_set ? `Set by the ${s.env} environment variable` : "Not set";
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" data-testid={`secret-${s.key}`}>
      <Badge tone={s.is_set ? "green" : "neutral"}>{s.is_set ? "Set" : "Not set"}</Badge>
      <span className="text-neutral-600 dark:text-neutral-400">{where}</span>
      {keysAction}
    </div>
  );
}

export default function SettingField({
  s,
  value,
  onChange,
  onReset,
  error,
  providers,
  keysAction,
}: {
  s: SettingView;
  value: unknown;
  onChange: (v: unknown) => void;
  onReset: () => void;
  error?: string;
  providers: ProviderStatus[] | null;
  keysAction?: ReactNode;
}) {
  const labelFor = s.type === "bool" || s.type === "model" || s.widget ? undefined : `setting-${s.key}`;
  return (
    <div className="space-y-2 py-4 first:pt-0 last:pb-0" data-testid={`setting-${s.key}`}>
      <div className="flex flex-wrap items-center gap-2">
        {labelFor ? (
          <label htmlFor={labelFor} className="text-sm font-medium">
            {s.label}
          </label>
        ) : (
          <span className="text-sm font-medium">{s.label}</span>
        )}
        {!s.secret && <SourceBadge s={s} />}
        {s.apply === "restart" && <Badge tone="violet">Needs restart</Badge>}
        {s.needs_restart && <Badge tone="amber">Restart required</Badge>}
        <span className="flex-1" />
        {s.source === "saved" && s.editable && (
          <button type="button" className="btn-ghost btn-sm" onClick={onReset} aria-label={`Reset ${s.label} to default`}>
            <RotateCcw className="h-3.5 w-3.5" /> Reset to default
          </button>
        )}
      </div>
      <p className="text-xs text-neutral-500 dark:text-neutral-400">{s.description}</p>
      {s.secret ? (
        <SecretStatus s={s} keysAction={keysAction} />
      ) : (
        <Control s={s} value={value} onChange={onChange} providers={providers} />
      )}
      {s.source === "env" && !s.secret && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          Set by the environment variable <code className="font-mono">{s.env}</code>, read only. Remove it there to change it here.
        </p>
      )}
      {!s.editable && s.source !== "env" && !s.secret && (
        <p className="text-xs text-neutral-500">Can only be set with the environment variable <code className="font-mono">{s.env}</code>.</p>
      )}
      {(error || s.error) && (
        <p role="alert" className="text-xs text-red-700 dark:text-red-400" data-testid={`error-${s.key}`}>
          {error || s.error}
        </p>
      )}
    </div>
  );
}
