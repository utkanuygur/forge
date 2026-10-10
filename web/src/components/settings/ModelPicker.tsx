import { useId, useState } from "react";
import { Check, CircleAlert } from "lucide-react";
import { modelPresets } from "../../api";
import type { ProviderStatus } from "../../settingsApi";

/** Is `model` (a Forge model string) listed by its provider right now? null when unknown. */
export function modelInstalled(model: string, providers: ProviderStatus[] | null): boolean | null {
  if (!providers || !model) return null;
  for (const p of providers) {
    if (model.startsWith(p.prefix)) {
      if (!p.reachable) return null;
      return p.models.includes(model.slice(p.prefix.length));
    }
  }
  return null;
}

function shortName(model: string): string {
  if (model.startsWith("anthropic:")) return model.slice("anthropic:".length);
  if (model.startsWith("openai_compat:")) return model.slice(model.lastIndexOf("|") + 1);
  return model;
}

const PRESETS_TAB = "presets";

/**
 * Model picker: provider tabs listing the models each provider reports (from /v1/models/available), a presets
 * tab with an "installed" check, and a free text field that always holds the actual value.
 */
export default function ModelPicker({
  id,
  label,
  value,
  onChange,
  providers,
  disabled,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  providers: ProviderStatus[] | null;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [tab, setTab] = useState<string>(PRESETS_TAB);
  const tabsId = useId();
  const installed = modelInstalled(value, providers);
  const current = providers?.find((p) => p.id === tab);
  const choices: { model: string; ok: boolean | null }[] =
    tab === PRESETS_TAB
      ? modelPresets(providers).map((m) => ({ model: m, ok: modelInstalled(m, providers) }))
      : (current?.models ?? []).map((m) => ({ model: current!.prefix + m, ok: true }));

  return (
    <div className="min-w-0 space-y-2" data-testid={`model-picker-${id}`}>
      <input
        id={id}
        className="input font-mono text-xs"
        value={value}
        disabled={disabled}
        spellCheck={false}
        placeholder={placeholder}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
      />
      {value && installed !== null && (
        <p className={`flex items-center gap-1 text-xs ${installed ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400"}`}>
          {installed ? <Check className="h-3.5 w-3.5" /> : <CircleAlert className="h-3.5 w-3.5" />}
          {installed ? "Installed and available" : "Not available on its provider right now"}
        </p>
      )}
      {!disabled && (
        <div>
          <div role="tablist" aria-label={`${label} sources`} className="flex flex-wrap gap-1 text-xs">
            {[{ id: PRESETS_TAB, name: "Presets" }, ...(providers ?? [])].map((p) => (
              <button
                key={p.id}
                type="button"
                role="tab"
                id={`${tabsId}-${p.id}`}
                aria-selected={tab === p.id}
                onClick={() => setTab(p.id)}
                className={`rounded-md px-2 py-1 ${
                  tab === p.id
                    ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900"
                    : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
                }`}
              >
                {p.name}
              </button>
            ))}
          </div>
          <div role="tabpanel" aria-labelledby={`${tabsId}-${tab}`} className="mt-2">
            {current && !current.reachable ? (
              <p className="text-xs text-neutral-500">
                {current.name} is not reachable{current.error ? ` (${current.error})` : ""}. You can still type a model string.
              </p>
            ) : current && choices.length === 0 ? (
              <p className="text-xs text-neutral-500">{current.name} reports no models.</p>
            ) : (
              <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                {choices.map(({ model, ok }) => (
                  <button
                    key={model}
                    type="button"
                    title={model}
                    onClick={() => onChange(model)}
                    className={`inline-flex max-w-full items-center gap-1 truncate rounded-md border px-2 py-0.5 font-mono text-xs ${
                      value === model
                        ? "border-accent-400 bg-accent-50 text-accent-700 dark:border-accent-600 dark:bg-accent-900/30 dark:text-accent-300"
                        : "border-neutral-200 text-neutral-600 hover:border-neutral-300 dark:border-neutral-700 dark:text-neutral-400"
                    }`}
                  >
                    {ok && <Check aria-label="installed" className="h-3 w-3 shrink-0 text-emerald-600" />}
                    <span className="truncate">{shortName(model)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
