import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { api, type Agent } from "../api";
import RunView from "../components/RunView";

export default function RunPage() {
  const { id } = useParams();
  const [agents, setAgents] = useState<Agent[] | null>(null);

  useEffect(() => {
    api.listAgents().then(setAgents).catch(() => setAgents([]));
  }, []);

  if (!id) return null;
  return (
    <RunView
      runId={id}
      header={(run) => {
        const agent = agents?.find((a) => a.id === run.agent_id || a.slug === run.agent_slug);
        return (
          <div>
            {agent && (
              <Link
                to={`/agents/${agent.slug}/runs`}
                className="mb-2 inline-flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
              >
                <ChevronLeft className="h-4 w-4" /> {agent.name}
              </Link>
            )}
            <h1 className="text-xl font-semibold tracking-tight">
              Run <span className="font-mono text-base text-neutral-500">{run.id}</span>
            </h1>
          </div>
        );
      }}
    />
  );
}
