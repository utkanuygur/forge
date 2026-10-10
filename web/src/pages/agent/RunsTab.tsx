import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { api, errMsg, fmtTime, isTerminal, type Agent, type Run } from "../../api";
import RunForm from "../../components/RunForm";
import StatusPill from "../../components/StatusPill";
import { Badge, Empty, ErrorBox, Loading, Section } from "../../components/ui";

function Rating({ run }: { run: Run }) {
  const fb = run.feedback || [];
  if (fb.length === 0) return <span className="text-neutral-300 dark:text-neutral-600">none</span>;
  const up = fb.filter((f) => f.rating === 1).length;
  const down = fb.length - up;
  return (
    <span className="inline-flex items-center gap-2 text-xs">
      {up > 0 && (
        <span className="inline-flex items-center gap-0.5 text-emerald-600">
          <ThumbsUp className="h-3.5 w-3.5" /> {up}
        </span>
      )}
      {down > 0 && (
        <span className="inline-flex items-center gap-0.5 text-red-600">
          <ThumbsDown className="h-3.5 w-3.5" /> {down}
        </span>
      )}
    </span>
  );
}

export default function RunsTab({ agent }: { agent: Agent }) {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const runsRef = useRef<Run[] | null>(null);
  runsRef.current = runs;

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await api.listRuns(agent.slug, 50);
        if (alive) {
          setRuns(r);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(errMsg(e));
      }
    }
    load();
    const t = setInterval(() => {
      // Refresh while anything is in flight.
      if (runsRef.current?.some((r) => !isTerminal(r.status))) load();
    }, 4000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [agent.slug]);

  return (
    <div className="space-y-8">
      <Section title="New run">
        <div className="card p-5">
          <RunForm
            slug={agent.slug}
            versions={agent.versions}
            prodVersion={agent.prod_version}
            onCreated={(r) => navigate(`/runs/${r.id}`)}
          />
        </div>
      </Section>
      <Section title="Recent runs">
        <ErrorBox error={error} />
        {runs === null && !error && <Loading />}
        {runs && runs.length === 0 && <Empty>No runs yet. Start one above, or call the API from the Deploy tab.</Empty>}
        {runs && runs.length > 0 && (
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead className="border-b border-neutral-200 dark:border-neutral-800">
                <tr>
                  <th className="th">Status</th>
                  <th className="th">Version</th>
                  <th className="th">Source</th>
                  <th className="th">Input</th>
                  <th className="th">Rating</th>
                  <th className="th">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {runs.map((r) => (
                  <tr
                    key={r.id}
                    onClick={() => navigate(`/runs/${r.id}`)}
                    className="cursor-pointer hover:bg-neutral-50 dark:hover:bg-neutral-800/50"
                  >
                    <td className="td">
                      <StatusPill status={r.status} />
                    </td>
                    <td className="td font-medium">v{r.version_n}</td>
                    <td className="td">
                      <Badge>{r.source}</Badge>
                    </td>
                    <td className="td max-w-[360px]">
                      <span className="line-clamp-2 text-neutral-700 dark:text-neutral-300">{r.input}</span>
                    </td>
                    <td className="td">
                      <Rating run={r} />
                    </td>
                    <td className="td whitespace-nowrap text-neutral-500">{fmtTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
