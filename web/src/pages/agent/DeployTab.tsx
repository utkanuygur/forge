import { useEffect, useState } from "react";
import { getPublicUrl } from "../../settingsApi";
import { RefreshCw } from "lucide-react";
import { api, errMsg, type Agent } from "../../api";
import ApiKeyCallout from "../../components/ApiKeyCallout";
import { CopyButton, ErrorBox, Section } from "../../components/ui";

function CodeBlock({ code }: { code: string }) {
  return (
    <div className="relative">
      <pre className="mono-block pr-20">{code}</pre>
      <div className="absolute right-2 top-2">
        <CopyButton text={code} />
      </div>
    </div>
  );
}

export default function DeployTab({ agent }: { agent: Agent; onChanged: () => void }) {
  // The public_url setting, when set, is the address others use (share links and API examples).
  const [publicUrl, setPublicUrl] = useState("");
  useEffect(() => {
    getPublicUrl().then(setPublicUrl);
  }, []);
  const origin = publicUrl || window.location.origin;
  const shareUrl = `${origin}/s/${agent.slug}?share=${encodeURIComponent(agent.share_token)}`;
  const [newKey, setNewKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function rotate() {
    if (!confirm("Rotate the API key? The current key stops working immediately.")) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.rotateKey(agent.slug);
      setNewKey(r.api_key);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  const curl = `curl -s ${origin}/v1/agents/${agent.slug}/runs \\
  -H "Authorization: Bearer $FORGE_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"input": "Describe the task here", "wait": true}'`;

  const curlFiles = `curl -s ${origin}/v1/agents/${agent.slug}/runs \\
  -H "Authorization: Bearer $FORGE_API_KEY" \\
  -F input="Fix the failing tests" \\
  -F wait=true \\
  -F files=@repo.tar.gz`;

  const webhook = `curl -s ${origin}/v1/agents/${agent.slug}/runs \\
  -H "Authorization: Bearer $FORGE_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"input": "...", "callback_url": "https://example.com/forge-hook"}'`;

  return (
    <div className="max-w-3xl space-y-8">
      <Section title="API key">
        <div className="card space-y-3 p-5">
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Runtime calls authenticate with <code className="font-mono text-xs">Authorization: Bearer &lt;api_key&gt;</code>. The key
            was shown once when the agent was created. If you lost it, rotate to get a new one.
          </p>
          {newKey && <ApiKeyCallout apiKey={newKey} title="New API key" onDismiss={() => setNewKey(null)} />}
          <ErrorBox error={error} />
          <button className="btn-secondary" onClick={rotate} disabled={busy}>
            <RefreshCw className="h-4 w-4" /> {busy ? "Rotating..." : "Rotate key"}
          </button>
        </div>
      </Section>

      <Section title="Share link">
        <div className="card space-y-3 p-5">
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Colleagues can run the production version from a simple page, without seeing its configuration. Their feedback shows up
            in Runs.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-md bg-neutral-100 px-2.5 py-1.5 font-mono text-xs dark:bg-neutral-950">
              {shareUrl}
            </code>
            <CopyButton text={shareUrl} />
            <a className="btn-secondary btn-sm" href={shareUrl} target="_blank" rel="noreferrer">
              Open
            </a>
          </div>
        </div>
      </Section>

      <Section title="Call over HTTP">
        <div className="card space-y-4 p-5">
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Run the production version and wait for the result. The response is a Run object with <code className="font-mono text-xs">status</code>{" "}
            and <code className="font-mono text-xs">output</code>.
          </p>
          <CodeBlock code={curl} />
          <p className="text-sm text-neutral-600 dark:text-neutral-400">Upload files as multipart. A single .tar.gz is extracted into the workspace.</p>
          <CodeBlock code={curlFiles} />
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Fetch the changed workspace with <code className="font-mono text-xs">GET /v1/runs/&lt;id&gt;/artifacts</code> and the diff
            with <code className="font-mono text-xs">GET /v1/runs/&lt;id&gt;/diff</code>.
          </p>
        </div>
      </Section>

      <Section title="Webhooks">
        <div className="card space-y-4 p-5">
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            For long tasks, omit <code className="font-mono text-xs">wait</code> and pass a <code className="font-mono text-xs">callback_url</code>.
            The call returns immediately with a queued run; when it finishes, Forge POSTs the final Run JSON to your URL.
          </p>
          <CodeBlock code={webhook} />
        </div>
      </Section>
    </div>
  );
}
