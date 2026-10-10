import { useState, type ReactNode } from "react";
import { Box, Cpu, KeyRound, Server } from "lucide-react";
import { errMsg } from "../../api";
import { flatSettings, settingsApi, type ProviderStatus, type SettingsPayload } from "../../settingsApi";

type Tone = "ok" | "warn" | "off";
const DOT: Record<Tone, string> = { ok: "bg-emerald-500", warn: "bg-amber-500", off: "bg-neutral-400" };

function Card({ title, icon, tone, status, children, testId }: { title: string; icon: ReactNode; tone: Tone; status: string; children?: ReactNode; testId: string }) {
  return (
    <div className="card min-w-0 p-4" data-testid={testId} data-tone={tone}>
      <div className="flex items-center gap-2 text-sm font-medium">
        <span className="text-neutral-500">{icon}</span>
        {title}
      </div>
      <div className="mt-2 flex items-center gap-2 text-sm">
        <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[tone]}`} aria-hidden="true" />
        <span className="min-w-0 break-words">{status}</span>
      </div>
      {children && <div className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">{children}</div>}
    </div>
  );
}

const isLocal = (url: string) => /\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/.test(url);

export function ollamaOf(providers: ProviderStatus[] | null) {
  return providers?.find((p) => p.kind === "openai_compat" && /:11434(\/|$)/.test(p.base_url)) ?? null;
}

export function gpuOf(providers: ProviderStatus[] | null) {
  return providers?.find((p) => p.kind === "openai_compat" && !isLocal(p.base_url)) ?? null;
}

export default function StatusCards({ payload, providers, keysAction }: { payload: SettingsPayload; providers: ProviderStatus[] | null; keysAction?: ReactNode }) {
  const key = flatSettings(payload).anthropic_api_key;
  const claude = providers?.find((p) => p.kind === "anthropic") ?? null;
  const [test, setTest] = useState<{ busy: boolean; text: string | null }>({ busy: false, text: null });
  const ollama = ollamaOf(providers);
  const gpu = gpuOf(providers);
  const docker = payload.info.docker;

  async function testKey() {
    setTest({ busy: true, text: null });
    try {
      const [p] = (await settingsApi.models({ refresh: true, provider: "anthropic" })).providers;
      setTest({ busy: false, text: p.reachable ? `The key works. ${p.models.length} models are available.` : `The key did not work: ${p.error}` });
    } catch (e) {
      setTest({ busy: false, text: errMsg(e) });
    }
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Card title="Claude key" icon={<KeyRound className="h-4 w-4" />} testId="status-claude" tone={key?.is_set ? (claude && !claude.reachable ? "warn" : "ok") : "off"} status={key?.is_set ? (key.from_desktop ? "Set in the Forge app" : "Set in the environment") : "Not set"}>
        <div className="flex flex-wrap items-center gap-2">
          {key?.is_set && (
            <button type="button" className="btn-secondary btn-sm" onClick={testKey} disabled={test.busy}>
              {test.busy ? "Testing..." : "Test"}
            </button>
          )}
          {keysAction}
        </div>
        {test.text && (
          <p className="mt-2" role="status">
            {test.text}
          </p>
        )}
      </Card>
      <Card title="Ollama" icon={<Cpu className="h-4 w-4" />} testId="status-ollama" tone={!ollama ? "off" : ollama.reachable ? "ok" : "warn"} status={!ollama ? "No endpoint on port 11434" : ollama.reachable ? `Running, ${ollama.models.length} models` : "Not reachable"}>
        {ollama?.reachable && ollama.models.length > 0 ? ollama.models.slice(0, 4).join(", ") + (ollama.models.length > 4 ? " and more" : "") : ollama?.error}
      </Card>
      <Card title="Docker" icon={<Box className="h-4 w-4" />} testId="status-docker" tone={docker.status === "ok" ? "ok" : docker.status === "skipped" ? "off" : "warn"} status={docker.status === "ok" ? `Running, image ${docker.image} present` : docker.status === "skipped" ? "Not used (local executor)" : "Problem"}>
        {docker.status === "ok" ? `Docker ${docker.server_version}` : docker.detail}
      </Card>
      <Card title="GPU endpoint" icon={<Server className="h-4 w-4" />} testId="status-gpu" tone={!gpu ? "off" : gpu.reachable ? "ok" : "warn"} status={!gpu ? "Not configured" : gpu.reachable ? `Reachable, ${gpu.models.length} models` : "Configured, not reachable"}>
        {gpu ? <span className="break-all font-mono">{gpu.base_url}</span> : "Add a remote OpenAI compatible endpoint, such as vLLM on Modal, under Models."}
      </Card>
    </div>
  );
}
