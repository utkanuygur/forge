import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { api, errMsg, fmtTime, isTerminal, type Agent, type Run } from "../../api";
import RunForm from "../../components/RunForm";
import StatusPill from "../../components/StatusPill";
import { Badge, Empty, ErrorBox, Loading, Section } from "../../components/ui";
import { fmtMs, fmtUsd } from "../../lib/format";
import { buildRunQuery, hasActiveFilters, type RunFilters } from "../../lib/runFilters";

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

const STATUS_OPTIONS = ["succeeded", "failed", "timeout", "budget_exceeded", "awaiting_approval", "running", "queued"];
const SOURCE_OPTIONS = ["ui", "api", "share", "eval", "agent", "schedule", "github", "slack", "local"];

function filtersFromParams(p: URLSearchParams): RunFilters {
  const sort = p.get("sort");
  const rating = p.get("rating");
  return {
    status: p.get("status") || "",
    version: p.get("version") || "",
    source: p.get("source") || "",
    rating: rating === "up" || rating === "down" || rating === "none" ? rating : "",
    toolErrors: p.get("has_tool_errors") === "true",
    q: p.get("q") || "",
    sort: sort === "duration" || sort === "cost" ? sort : "created_at",
  };
}

function FilterSelect({ label, value, onChange, children }: { label: string; value: string; onChange: (v: string) => void; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs text-neutral-500 dark:text-neutral-400">
      {label}
      <select aria-label={`${label} filter`} className="input py-1.5 text-sm" value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </label>
  );
}

export default function RunsTab({ agent }: { agent: Agent }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState<RunFilters>(() => filtersFromParams(params));
  const [qInput, setQInput] = useState(filters.q || "");
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const runsRef = useRef<Run[] | null>(null);
  runsRef.current = runs;
  const query = buildRunQuery({ ...filters, limit: 50 });

  // Debounce the text search.
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => (f.q === qInput ? f : { ...f, q: qInput })), 300);
    return () => clearTimeout(t);
  }, [qInput]);

  // Keep the filters in the URL so links (for example from the dashboard) open a filtered view.
  useEffect(() => {
    const next = buildRunQuery(filters);
    if (next !== params.toString()) setParams(new URLSearchParams(next), { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await api.searchRuns(agent.slug, query);
        if (alive) {
          setRuns(r.items);
          setTotal(r.total);
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
  }, [agent.slug, query]);

  const set = (patch: Partial<RunFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const active = hasActiveFilters(filters);
  const versions = [...(agent.versions ?? [])].sort((a, b) => b.n - a.n);

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
      <Section
        title="Recent runs"
        actions={
          runs && (
            <span className="text-xs text-neutral-500 dark:text-neutral-400" data-testid="run-count">
              {total === runs.length ? `${total} ${total === 1 ? "run" : "runs"}` : `Showing ${runs.length} of ${total} runs`}
            </span>
          )
        }
      >
        <div className="card grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 lg:grid-cols-7" role="search" aria-label="Run filters">
          <label className="col-span-2 flex min-w-0 flex-col gap-1 text-xs text-neutral-500 dark:text-neutral-400 sm:col-span-3 lg:col-span-2">
            Search
            <input
              className="input py-1.5"
              type="search"
              placeholder="Input or output text"
              aria-label="Search runs"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
            />
          </label>
          <FilterSelect label="Status" value={filters.status || ""} onChange={(v) => set({ status: v })}>
            <option value="">Any status</option>
            <option value="failed,timeout">Failed or timeout</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect label="Version" value={filters.version || ""} onChange={(v) => set({ version: v })}>
            <option value="">Any version</option>
            {versions.map((v) => (
              <option key={v.n} value={String(v.n)}>
                v{v.n}
                {v.n === agent.prod_version ? " (prod)" : ""}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect label="Source" value={filters.source || ""} onChange={(v) => set({ source: v })}>
            <option value="">Any source</option>
            {SOURCE_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect label="Rating" value={filters.rating || ""} onChange={(v) => set({ rating: v as RunFilters["rating"] })}>
            <option value="">Any rating</option>
            <option value="up">Thumbs up</option>
            <option value="down">Thumbs down</option>
            <option value="none">Not rated</option>
          </FilterSelect>
          <FilterSelect label="Sort" value={filters.sort || "created_at"} onChange={(v) => set({ sort: v as RunFilters["sort"] })}>
            <option value="created_at">Newest</option>
            <option value="duration">Slowest</option>
            <option value="cost">Most expensive</option>
          </FilterSelect>
          <label className="col-span-2 flex items-center gap-2 text-sm sm:col-span-3 lg:col-span-7">
            <input type="checkbox" checked={!!filters.toolErrors} onChange={(e) => set({ toolErrors: e.target.checked })} />
            Only runs with tool errors
            {active && (
              <button
                type="button"
                className="btn-ghost btn-sm ml-auto"
                onClick={() => {
                  setQInput("");
                  setFilters({ sort: filters.sort });
                }}
              >
                Clear filters
              </button>
            )}
          </label>
        </div>
        <ErrorBox error={error} />
        {runs === null && !error && <Loading />}
        {runs && runs.length === 0 && !active && <Empty>No runs yet. Start one above, or call the API from the Deploy tab.</Empty>}
        {runs && runs.length === 0 && active && <Empty>No runs match these filters.</Empty>}
        {runs && runs.length > 0 && (
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[760px]">
              <thead className="border-b border-neutral-200 dark:border-neutral-800">
                <tr>
                  <th className="th">Status</th>
                  <th className="th">Version</th>
                  <th className="th">Source</th>
                  <th className="th">Input</th>
                  <th className="th">Rating</th>
                  <th className="th">Duration</th>
                  <th className="th">Cost</th>
                  <th className="th">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {runs.map((r) => (
                  <tr
                    key={r.id}
                    data-testid="run-row"
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
                    <td className="td whitespace-nowrap tabular-nums text-neutral-600 dark:text-neutral-400">{fmtMs(r.duration_ms)}</td>
                    <td className="td whitespace-nowrap tabular-nums text-neutral-600 dark:text-neutral-400">
                      {r.cost_usd === undefined ? "" : r.cost_usd === null ? "unknown" : fmtUsd(r.cost_usd)}
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
