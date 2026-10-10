import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { api, errMsg, type Agent } from "../api";
import RunView from "../components/RunView";
import Logo from "../components/Logo";
import ShareChat from "../components/share/ShareChat";
import { ErrorBox, Loading } from "../components/ui";

/**
 * Colleague-facing page: /s/:slug?share=<token>. A conversation with the agent's production version; the open
 * conversation is `?c=<session id>`. Links to one shot runs from before conversations (`?run=`) still open.
 * No configuration or instructions are ever shown.
 */
export default function SharePage() {
  const { slug = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const share = params.get("share") || undefined;
  const runId = params.get("run");
  const conversation = params.get("c");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getAgent(slug, share)
      .then(setAgent)
      .catch((e) => setError(errMsg(e)));
  }, [slug, share]);

  const setParam = useCallback(
    (key: string, value: string | null) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          return next;
        },
        { replace: key === "c" },
      );
    },
    [setParams],
  );

  return (
    <div className="min-h-screen">
      <header className="border-b border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-3 text-sm text-neutral-500 sm:px-6">
          <Logo size={20} /> <span className="font-medium text-neutral-700 dark:text-neutral-300">Forge</span>
        </div>
      </header>
      <main className="mx-auto max-w-3xl space-y-4 px-4 py-5 sm:px-6">
        {!share && <ErrorBox error="This link is missing its share token. Ask the agent owner for a fresh link." />}
        {error && !agent && <ErrorBox error={error} />}
        {share && !agent && !error && <Loading />}
        {agent && (
          <div>
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{agent.name}</h1>
            {agent.description && <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">{agent.description}</p>}
          </div>
        )}
        {runId && share ? (
          <>
            <button className="btn-ghost -ml-3" onClick={() => setParam("run", null)}>
              <ArrowLeft className="h-4 w-4" /> Back to the conversation
            </button>
            <RunView runId={runId} share={share} simple />
          </>
        ) : (
          agent &&
          share && (
            <ShareChat
              key={`${slug}:${share}`}
              agent={agent}
              share={share}
              initialSession={conversation}
              onSessionChange={(id) => setParam("c", id)}
            />
          )
        )}
      </main>
    </div>
  );
}
