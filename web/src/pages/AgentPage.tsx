import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { api, errMsg, type Agent } from "../api";
import Tabs from "../components/Tabs";
import { Badge, ErrorBox, Loading } from "../components/ui";
import ConfigureTab from "./agent/ConfigureTab";
import RunsTab from "./agent/RunsTab";
import MonitoringTab from "./agent/MonitoringTab";
import EvalsTab from "./agent/EvalsTab";
import DeployTab from "./agent/DeployTab";
import { BudgetBanner, BudgetMeter, PendingApprovalsBadge, useGovernance } from "../components/Governance";

const TABS = [
  { key: "configure", label: "Configure" },
  { key: "runs", label: "Runs" },
  { key: "monitoring", label: "Monitoring" },
  { key: "evals", label: "Evaluations" },
  { key: "deploy", label: "Deploy" },
];

export default function AgentPage() {
  const { slug = "", tab = "configure" } = useParams();
  const navigate = useNavigate();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const gov = useGovernance(slug, agent?.monthly_budget_usd);
  const tabs = TABS.map((t) => (t.key === "runs" ? { ...t, badge: gov?.pending_approvals || 0 } : t));

  const reload = useCallback(async () => {
    try {
      setAgent(await api.getAgent(slug));
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [slug]);

  useEffect(() => {
    setAgent(null);
    reload();
  }, [reload]);

  if (error && !agent) return <ErrorBox error={error} />;
  if (!agent) return <Loading label="Loading agent" />;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/" className="mb-2 inline-flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200">
          <ChevronLeft className="h-4 w-4" /> Agents
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{agent.name}</h1>
          <span className="font-mono text-sm text-neutral-500">{agent.slug}</span>
          <Badge tone="green">prod v{agent.prod_version}</Badge>
          {agent.auto_promote && <Badge tone="violet">auto promote</Badge>}
          {gov && <PendingApprovalsBadge gov={gov} slug={slug} />}
          {gov && <BudgetMeter gov={gov} />}
        </div>
        {agent.description && <p className="mt-1 text-neutral-600 dark:text-neutral-400">{agent.description}</p>}
      </div>
      {gov && <BudgetBanner gov={gov} />}
      <Tabs tabs={tabs} active={tab} onChange={(k) => navigate(`/agents/${slug}/${k}`)} />
      {tab === "configure" && <ConfigureTab agent={agent} onChanged={reload} />}
      {tab === "runs" && <RunsTab agent={agent} />}
      {tab === "monitoring" && <MonitoringTab agent={agent} />}
      {tab === "evals" && <EvalsTab agent={agent} onChanged={reload} />}
      {tab === "deploy" && <DeployTab agent={agent} onChanged={reload} />}
    </div>
  );
}
