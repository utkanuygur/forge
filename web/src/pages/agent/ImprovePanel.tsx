import { useCallback, useEffect, useState } from "react";
import { Scissors, Sparkles } from "lucide-react";
import { api, errMsg, fmtTime, type Agent, type Improvement } from "../../api";
import StatusPill from "../../components/StatusPill";
import { useToast } from "../../components/Toast";
import { Badge, Empty, ErrorBox, Section } from "../../components/ui";
import ImprovementView from "./ImprovementView";

export default function ImprovePanel({
  agent,
  hasCases,
  hasTrainCases = hasCases,
  prodLessons = 0,
  onChanged,
}: {
  agent: Agent;
  hasCases: boolean;
  hasTrainCases?: boolean;
  prodLessons?: number;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [list, setList] = useState<Improvement[] | null>(null);
  const [current, setCurrent] = useState<Improvement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    try {
      const xs = await api.listImprovements(agent.slug);
      setList(xs);
      return xs;
    } catch (e) {
      setError(errMsg(e));
      return null;
    }
  }, [agent.slug]);

  useEffect(() => {
    loadList().then((xs) => {
      if (xs && xs.length > 0) setCurrent((c) => c ?? xs[0]);
    });
  }, [loadList]);

  // Poll the selected improvement while it runs.
  const currentId = current?.id;
  const running = current?.status === "running";
  useEffect(() => {
    if (!currentId || !running) return;
    const t = setInterval(async () => {
      try {
        const im = await api.getImprovement(currentId);
        setCurrent(im);
        if (im.status !== "running") {
          loadList();
          if (im.status === "promoted") onChanged();
        }
      } catch (e) {
        setError(errMsg(e));
      }
    }, 2000);
    return () => clearInterval(t);
  }, [currentId, running, loadList, onChanged]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const im = await api.improve(agent.slug);
      setCurrent(im);
      loadList();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function startPrune() {
    setBusy(true);
    setError(null);
    try {
      const im = await api.prune(agent.slug);
      setCurrent(im);
      loadList();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function promote(n: number) {
    setError(null);
    try {
      await api.promote(agent.slug, n);
      toast(`v${n} is now in production`);
      onChanged();
      if (current) setCurrent({ ...current, status: "promoted" });
      loadList();
    } catch (e) {
      setError(errMsg(e));
    }
  }

  return (
    <Section
      title="Improve"
      actions={
        <div className="flex gap-2">
          <button
            className="btn-secondary"
            onClick={startPrune}
            disabled={busy || running || !hasTrainCases || prodLessons < 2}
            title={prodLessons < 2 ? "Pruning needs at least 2 lessons in production." : undefined}
          >
            <Scissors className="h-4 w-4" /> {running && current?.kind === "prune" ? "Pruning..." : "Prune lessons"}
          </button>
          <button className="btn-primary" onClick={start} disabled={busy || running || !hasCases}>
            <Sparkles className="h-4 w-4" /> {running && current?.kind !== "prune" ? "Improving..." : busy ? "Starting..." : "Improve"}
          </button>
        </div>
      }
    >
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        The optimizer studies failing train cases and negative feedback, proposes lesson changes as a candidate version, then evaluates
        base and candidate on every case. A candidate is eligible only if it passes more cases and regresses none.
      </p>
      <p className="text-sm text-neutral-600 dark:text-neutral-400" data-testid="prune-explainer">
        Prune lessons tries production without each lesson on train cases and drops the ones that never helped, for a smaller prompt
        at equal quality.
      </p>
      <ErrorBox error={error} onDismiss={() => setError(null)} />
      {current && (
        <ImprovementView
          im={current}
          prodVersion={agent.prod_version}
          onPromote={promote}
        />
      )}
      {list && list.length === 0 && !current && (
        <Empty>{hasCases ? "No improvement cycles yet. Press Improve to run one." : "Add evaluation cases before running Improve."}</Empty>
      )}
      {list && list.length > 0 && (
        <div className="space-y-2 pt-4">
          <h3 className="text-sm font-medium text-neutral-600 dark:text-neutral-400">Previous improvements</h3>
          <ul className="card divide-y divide-neutral-100 dark:divide-neutral-800">
            {list.map((im) => (
              <li key={im.id}>
                <button
                  onClick={() => setCurrent(im)}
                  className={`flex w-full flex-wrap items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800/50 ${
                    current?.id === im.id ? "bg-accent-50/60 dark:bg-accent-900/20" : ""
                  }`}
                >
                  <StatusPill status={im.status} />
                  {im.kind === "prune" && <Badge tone="amber">prune</Badge>}
                  <span className="font-medium">
                    v{im.base_version} to {im.candidate_version ? `v${im.candidate_version}` : "..."}
                  </span>
                  {im.base && im.candidate && (
                    <span className="text-xs text-neutral-500">
                      holdout {im.base.holdout.passed}/{im.base.holdout.total} to {im.candidate.holdout.passed}/{im.candidate.holdout.total}
                    </span>
                  )}
                  <span className="flex-1" />
                  <span className="text-xs text-neutral-400">{fmtTime(im.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}
