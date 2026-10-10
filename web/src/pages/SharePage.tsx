import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { api, errMsg, type Agent } from "../api";
import RunForm from "../components/RunForm";
import RunView from "../components/RunView";
import Logo from "../components/Logo";
import { ErrorBox, Loading } from "../components/ui";

/** Colleague-facing page: /s/:slug?share=<token>. No configuration is shown. */
export default function SharePage() {
  const { slug = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const share = params.get("share") || undefined;
  const runId = params.get("run");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getAgent(slug, share)
      .then(setAgent)
      .catch((e) => setError(errMsg(e)));
  }, [slug, share]);

  function setRun(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("run", id);
    else next.delete("run");
    setParams(next);
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-4 text-sm text-neutral-500 sm:px-6">
          <Logo size={20} /> <span className="font-medium text-neutral-700 dark:text-neutral-300">Forge</span>
        </div>
      </header>
      <main className="mx-auto max-w-3xl space-y-6 px-4 py-8 sm:px-6">
        {!share && <ErrorBox error="This link is missing its share token. Ask the agent owner for a fresh link." />}
        {error && !agent && <ErrorBox error={error} />}
        {!agent && !error && <Loading />}
        {agent && (
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{agent.name}</h1>
            {agent.description && <p className="mt-1 text-neutral-600 dark:text-neutral-400">{agent.description}</p>}
          </div>
        )}
        {runId ? (
          <>
            <button className="btn-ghost -ml-3" onClick={() => setRun(null)}>
              <ArrowLeft className="h-4 w-4" /> New request
            </button>
            <RunView runId={runId} share={share} simple />
          </>
        ) : (
          (agent || error) && (
            <div className="card p-5">
              <RunForm slug={slug} share={share} onCreated={(r) => setRun(r.id)} placeholder="What would you like the agent to do?" />
            </div>
          )
        )}
      </main>
    </div>
  );
}
