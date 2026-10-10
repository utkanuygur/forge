import { matchPath } from "react-router-dom";
import type { CopilotContext } from "../../copilotApi";

export const TAB_LABELS: Record<string, string> = {
  configure: "Configure",
  playground: "Playground",
  runs: "Runs",
  monitoring: "Monitoring",
  evals: "Evaluations",
  deploy: "Deploy",
};

export interface PageInfo {
  context: CopilotContext;
  /** Where the user is, e.g. "Evaluations", "Run r_ab12", "Dashboard"; the agent name is added separately. */
  place: string;
  slug?: string;
  starters: { label: string; text: string }[];
}

/** What the assistant should know about the current page, and starter prompts that fit it. */
export function pageInfo(pathname: string): PageInfo {
  const withTab = matchPath("/agents/:slug/:tab", pathname);
  const agent = withTab || matchPath("/agents/:slug", pathname);
  if (agent?.params.slug) {
    const slug = agent.params.slug;
    const tab = withTab?.params.tab || "configure";
    const place = TAB_LABELS[tab] || tab;
    const ctx: CopilotContext = { page: pathname, agent_slug: slug };
    const starters: Record<string, { label: string; text: string }[]> = {
      evals: [
        { label: "Add eval cases", text: `Add three eval cases to ${slug} that cover its most common tasks.` },
        { label: "Why are these failing?", text: `Why are ${slug}'s failing eval cases failing?` },
        { label: "Run evaluation", text: `Run the evaluation of ${slug}'s production version.` },
      ],
      runs: [
        { label: "Why did the last run fail?", text: `Why did ${slug}'s most recent failed run fail?` },
        { label: "Summarize recent runs", text: `Summarize ${slug}'s recent runs.` },
      ],
      monitoring: [
        { label: "How is it doing?", text: `How is ${slug} doing this week?` },
        { label: "What costs the most?", text: `What drives ${slug}'s cost?` },
      ],
      deploy: [
        { label: "Run it on a schedule", text: `Run ${slug} every weekday at 9am UTC with a daily check task.` },
        { label: "How do I call it?", text: "How do I start a run of this agent from my own code?" },
      ],
      playground: [{ label: "What is the playground?", text: "How do playground sessions differ from runs?" }],
      configure: [
        { label: "Explain this agent", text: `Explain how ${slug} is configured.` },
        { label: "Ask before rm -rf", text: `Set an approval rule on ${slug} asking before bash commands with rm -rf.` },
        { label: "Set a budget", text: `Set a monthly budget of $20 for ${slug}.` },
      ],
    };
    return { context: ctx, place, slug, starters: starters[tab] || starters.configure };
  }
  const run = matchPath("/runs/:id", pathname);
  if (run?.params.id) {
    const id = run.params.id;
    return {
      context: { page: pathname, run_id: id },
      place: `Run ${id}`,
      starters: [
        { label: "Why did this run fail?", text: `Why did run ${id} fail?` },
        { label: "Save as eval case", text: `Save run ${id} as a train eval case.` },
      ],
    };
  }
  const general = [
    { label: "Which agent needs attention?", text: "Which agent is failing most often, and why?" },
    { label: "How do evals work?", text: "How do evaluations and the promotion rule work?" },
  ];
  if (pathname.startsWith("/dashboard")) return { context: { page: pathname }, place: "Dashboard", starters: general };
  return { context: { page: pathname }, place: "Agents", starters: general };
}
