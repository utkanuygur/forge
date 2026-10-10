import { modelPresets, TOOL_NAMES } from "../api";
import { useAvailableModels } from "../settingsApi";
import { Toggle } from "./ui";

export function ModelField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  // Local presets come from the models the configured endpoints report as installed.
  const presets = modelPresets(useAvailableModels());
  return (
    <div>
      <label className="label" htmlFor="model">
        Model
      </label>
      <input
        id="model"
        className="input font-mono"
        list="model-presets"
        value={value}
        placeholder="Server default"
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id="model-presets">
        {presets.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {presets.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => onChange(m)}
            className={`rounded-md border px-2 py-0.5 font-mono text-xs transition ${
              value === m
                ? "border-accent-400 bg-accent-50 text-accent-700 dark:border-accent-600 dark:bg-accent-900/30 dark:text-accent-300"
                : "border-neutral-200 text-neutral-600 hover:border-neutral-300 dark:border-neutral-700 dark:text-neutral-400"
            }`}
          >
            {m.length > 40 ? m.replace("openai_compat:", "") : m.replace("anthropic:", "")}
          </button>
        ))}
      </div>
    </div>
  );
}

export function ToolsField({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset>
      <legend className="label">Tools</legend>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {TOOL_NAMES.map((t) => (
          <label
            key={t}
            className="flex cursor-pointer items-center gap-2 rounded-lg border border-neutral-200 px-3 py-2 text-sm dark:border-neutral-800"
          >
            <input
              type="checkbox"
              className="h-4 w-4 accent-accent-600"
              checked={value.includes(t)}
              onChange={(e) => onChange(e.target.checked ? [...value, t] : value.filter((x) => x !== t))}
            />
            <span className="font-mono text-xs">{t}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function NetworkField({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return <Toggle checked={value} onChange={onChange} label="Network access" hint="Allow the sandbox to reach the internet." />;
}
