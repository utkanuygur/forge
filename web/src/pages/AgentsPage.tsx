import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bot, Plus } from "lucide-react";
import { api, errMsg, type Agent, type AgentCreated } from "../api";
import NewAgentForm from "../components/NewAgentForm";
import ApiKeyCallout from "../components/ApiKeyCallout";
import ModelWarning from "../components/settings/ModelWarning";
import { Badge, Empty, ErrorBox, Loading, Modal } from "../components/ui";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<AgentCreated | null>(null);

  async function load() {
    try {
      setAgents(await api.listAgents());
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  }
  useEffect(() => {
    load();
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
          <p className="text-sm text-neutral-500 dark:text-neutral-400">Build, deploy, and improve general purpose agents.</p>
        </div>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" /> New agent
        </button>
      </div>

      {created && (
        <ApiKeyCallout
          apiKey={created.api_key}
          title={`API key for ${created.name}`}
          onDismiss={() => setCreated(null)}
        />
      )}
      {created?.warning && <ModelWarning text={created.warning} />}
      <ErrorBox error={error} />

      {agents === null && !error && <Loading />}
      {agents && agents.length === 0 && <Empty>No agents yet. Create one from scratch or start from a template.</Empty>}
      {agents && agents.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {agents.map((a) => (
            <Link
              key={a.id}
              data-testid="agent-card"
              data-slug={a.slug}
              to={`/agents/${a.slug}`}
              className="card group flex flex-col gap-2 p-5 transition hover:border-accent-300 hover:shadow-md dark:hover:border-accent-700"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-50 text-accent-600 dark:bg-accent-900/30 dark:text-accent-400">
                    <Bot className="h-4 w-4" />
                  </div>
                  <div>
                    <div className="font-medium group-hover:text-accent-700 dark:group-hover:text-accent-300">{a.name}</div>
                    <div className="font-mono text-xs text-neutral-500">{a.slug}</div>
                  </div>
                </div>
                <Badge tone="green">prod v{a.prod_version}</Badge>
              </div>
              <p className="line-clamp-2 text-sm text-neutral-600 dark:text-neutral-400">
                {a.description || <span className="italic text-neutral-400">No description</span>}
              </p>
              {a.auto_promote && (
                <div>
                  <Badge tone="violet">auto promote</Badge>
                </div>
              )}
            </Link>
          ))}
        </div>
      )}

      {creating && (
        <Modal title="New agent" onClose={() => setCreating(false)} wide>
          <NewAgentForm
            onCancel={() => setCreating(false)}
            onCreated={(a) => {
              setCreating(false);
              setCreated(a);
              load();
            }}
          />
        </Modal>
      )}
    </div>
  );
}
