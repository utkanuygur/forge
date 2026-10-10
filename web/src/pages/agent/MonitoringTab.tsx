import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, type Agent, type AgentMetrics, type Delta, type VersionHealth } from "../../api";
import { ChartCard, EmptyChart, Legend, LineChart, RankBars, StackedBarChart, STATUS_KEYS, SWATCH_BG, type Marker } from "../../components/charts/Charts";
import { KpiRow, PeriodSelector } from "../../components/Kpis";
import StatusPill from "../../components/StatusPill";
import { Badge, ErrorBox, Loading, Section, Toggle } from "../../components/ui";
import { fmtDelta, fmtMs, fmtNum, fmtPct, fmtUsd, TONE_CLASSES, type MetricKind } from "../../lib/format";
import { periodLabel, tzOffsetMin, usePeriod } from "../../lib/period";
import { bucketSeconds, SeriesTable } from "../DashboardPage";
import { fmtTime } from "../../api";

function DeltaChip({ d, kind, better }: { d?: Delta; kind: MetricKind; better: "up" | "down" | "none" }) {
  if (!d || d.change === null) return null;
  const f = fmtDelta(d, kind, better);
  return <div className={`text-[11px] ${TONE_CLASSES[f.tone]}`} data-testid="version-delta">{f.text}</div>;
}

function score(s?: { passed: number; total: number; pass_rate: number | null } | null): ReactNode {
  if (!s || !s.total) return <span className="text-neutral-400">n/a</span>;
  return (
    <span>
      {fmtPct(s.pass_rate, 0)} <span className="text-neutral-400">({s.passed}/{s.total})</span>
    </span>
  );
}

function RunLinks({ ids }: { ids: string[] }) {
  return (
    <span className="inline-flex flex-wrap gap-x-2">
      {ids.map((id) => (
        <Link key={id} to={`/runs/${id}`} className="font-mono hover:underline" data-testid="example-run">
          {id.slice(0, 8)}
        </Link>
      ))}
    </span>
  );
}

function VersionTable({ m }: { m: AgentMetrics }) {
  const prevN = m.previous_prod_version;
  return (
    <div className="card overflow-x-auto" data-testid="version-table">
      <table className="w-full min-w-[960px] tabular-nums">
        <thead className="border-b border-neutral-200 dark:border-neutral-800">
          <tr>
            <th className="th">Version</th>
            <th className="th">Runs</th>
            <th className="th">Success</th>
            <th className="th">p50</th>
            <th className="th">p95</th>
            <th className="th">Avg turns</th>
            <th className="th">Thumbs up</th>
            <th className="th">Feedback</th>
            <th className="th">Cost per run</th>
            <th className="th">Train pass</th>
            <th className="th">Held out pass</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
          {m.versions.map((v: VersionHealth) => {
            const d = v.delta_vs_previous_prod;
            const h = v.health;
            return (
              <tr
                key={v.version}
                data-testid="version-row"
                data-version={v.version}
                data-prod={v.is_prod}
                className={v.is_prod ? "bg-accent-50/70 dark:bg-accent-900/20" : ""}
              >
                <td className="td whitespace-nowrap">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">v{v.version}</span>
                    {v.is_prod && <Badge tone="green">prod</Badge>}
                    {v.is_previous_prod && <Badge>previous prod</Badge>}
                  </div>
                  <div className="mt-0.5 max-w-[14rem] truncate font-mono text-xs text-neutral-500" title={v.model}>{v.model}</div>
                </td>
                <td className="td">{h.runs}</td>
                <td className="td">
                  {fmtPct(h.success_rate, 0)}
                  <DeltaChip d={d?.success_rate} kind="rate" better="up" />
                </td>
                <td className="td whitespace-nowrap">
                  {fmtMs(h.p50_ms)}
                  <DeltaChip d={d?.p50_ms} kind="duration" better="down" />
                </td>
                <td className="td whitespace-nowrap">
                  {fmtMs(h.p95_ms)}
                  <DeltaChip d={d?.p95_ms} kind="duration" better="down" />
                </td>
                <td className="td">
                  {fmtNum(h.avg_turns)}
                  <DeltaChip d={d?.avg_turns} kind="count" better="down" />
                </td>
                <td className="td">
                  {fmtPct(h.thumbs_up_rate, 0)}
                  <DeltaChip d={d?.thumbs_up_rate} kind="rate" better="up" />
                </td>
                <td className="td">{h.feedback_count}</td>
                <td className="td whitespace-nowrap">
                  {v.price.kind === "unknown" ? <span className="text-amber-700 dark:text-amber-400">unknown price</span> : fmtUsd(h.cost_per_run)}
                  {v.price.kind === "self_hosted" && <div className="text-[11px] text-neutral-500">self-hosted, not metered</div>}
                  <DeltaChip d={d?.cost_per_run} kind="usd" better="down" />
                </td>
                <td className="td whitespace-nowrap">
                  {score(v.eval?.train)}
                  <DeltaChip d={d?.train_pass_rate} kind="rate" better="up" />
                </td>
                <td className="td whitespace-nowrap">
                  {score(v.eval?.holdout)}
                  <DeltaChip d={d?.holdout_pass_rate} kind="rate" better="up" />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="border-t border-neutral-200 px-4 py-2 text-xs text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
        Production metrics over each version's lifetime{m.include_eval ? ", including eval runs" : ", eval runs excluded"}. Pass rates come from the latest
        evaluation of each version. {prevN ? `Changes on the prod row compare with v${prevN}, the previous prod version.` : "No previous prod version to compare with yet."}
      </p>
    </div>
  );
}

export default function MonitoringTab({ agent }: { agent: Agent }) {
  const [days, setDays] = usePeriod();
  const [includeEval, setIncludeEval] = useState(false);
  const [m, setM] = useState<AgentMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failVersion, setFailVersion] = useState<number | "all">("all");

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .agentMetrics(agent.slug, days, includeEval, tzOffsetMin())
        .then((x) => alive && (setM(x), setError(null)))
        .catch((e) => alive && setError(errMsg(e)));
    load();
    const t = setInterval(load, 15_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [agent.slug, days, includeEval]);

  const markers: Marker[] = useMemo(
    () =>
      (m?.promotions ?? [])
        .filter((p) => p.source !== "create" && p.at >= (m?.since ?? 0))
        .map((p) => ({ at: p.at, label: `v${p.version}` })),
    [m],
  );

  if (error && !m) return <ErrorBox error={error} />;
  if (!m) return <Loading label="Loading monitoring" />;

  const label = periodLabel(days);
  const empty = m.summary.runs === 0;
  const failures = failVersion === "all" ? m.failures.all : m.failures.versions.find((v) => v.version === failVersion) ?? m.failures.all;
  const size = bucketSeconds(m.bucket);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PeriodSelector days={days} onChange={setDays} />
        <Toggle checked={includeEval} onChange={setIncludeEval} label="Include eval runs" hint="Production metrics show real usage only by default" />
      </div>
      <ErrorBox error={error} />
      <KpiRow kpis={m.kpis} periodLabel={label} />

      <Section title="Version health">
        <VersionTable m={m} />
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title={`Runs per ${m.bucket}`}
          subtitle={markers.length ? "Violet lines mark promotions to prod" : "By final status"}
          legend={<Legend keys={STATUS_KEYS.slice(0, 3).map((k) => ({ label: k.label, swatch: SWATCH_BG[k.key] }))} />}
          table={<SeriesTable series={m.series} bucket={m.bucket} />}
          testId="monitor-runs-chart"
        >
          {empty ? <EmptyChart /> : <StackedBarChart points={m.series as never} keys={STATUS_KEYS} bucket={m.bucket} bucketSize={size} markers={markers} label="Runs by status over time" />}
        </ChartCard>
        <ChartCard title="Success rate" subtitle="Succeeded over finished runs in each bucket" testId="monitor-success-chart">
          {empty ? (
            <EmptyChart />
          ) : (
            <LineChart points={m.series as never} valueKey="success_rate" bucket={m.bucket} bucketSize={size} markers={markers} yFormat={(v) => fmtPct(v, 0)} label="Success rate over time" />
          )}
        </ChartCard>
      </div>

      <Section title="Cost">
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title={`Spend per ${m.bucket}`} subtitle={`Total ${fmtUsd(m.cost.total_usd)} in the last ${label}`} table={<SeriesTable series={m.series} bucket={m.bucket} cost />}>
            {empty ? (
              <EmptyChart />
            ) : (
              <StackedBarChart
                points={m.series as never}
                keys={[{ key: "cost_usd", label: "Spend", fill: "fill-accent-500" }]}
                bucket={m.bucket}
                bucketSize={size}
                markers={markers}
                integer={false}
                yFormat={fmtUsd}
                tooltipFormat={fmtUsd}
                label="Spend over time"
              />
            )}
          </ChartCard>
          <div className="card min-w-0 overflow-x-auto" data-testid="cost-versions">
            <table className="w-full min-w-[460px] tabular-nums">
              <thead className="border-b border-neutral-200 dark:border-neutral-800">
                <tr>
                  <th className="th">Version</th>
                  <th className="th">Runs</th>
                  <th className="th">Tokens in / out</th>
                  <th className="th">Spend</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {m.cost.versions.length === 0 && (
                  <tr>
                    <td className="td text-neutral-500" colSpan={4}>No runs in this period yet</td>
                  </tr>
                )}
                {m.cost.versions.map((c) => (
                  <tr key={c.version}>
                    <td className="td font-medium">v{c.version}</td>
                    <td className="td">{c.runs}</td>
                    <td className="td whitespace-nowrap">
                      {c.tokens_in.toLocaleString()} / {c.tokens_out.toLocaleString()}
                    </td>
                    <td className="td whitespace-nowrap">
                      {c.price.kind === "unknown" ? <span className="text-amber-700 dark:text-amber-400">unknown price</span> : fmtUsd(c.cost_usd)}
                      {c.price.note && c.price.kind !== "unknown" && <div className="text-[11px] text-neutral-500">{c.price.note}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card overflow-x-auto" data-testid="cost-improvements">
          <table className="w-full min-w-[720px] tabular-nums">
            <thead className="border-b border-neutral-200 dark:border-neutral-800">
              <tr>
                <th className="th">Improvement cycle</th>
                <th className="th">Status</th>
                <th className="th">Eval runs</th>
                <th className="th">Eval spend</th>
                <th className="th">Judge</th>
                <th className="th">Optimizer</th>
                <th className="th">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
              {m.cost.improvements.length === 0 && (
                <tr>
                  <td className="td text-neutral-500" colSpan={7}>No improvement cycles in this period</td>
                </tr>
              )}
              {m.cost.improvements.map((c) => (
                <tr key={c.id} data-testid="improvement-cost">
                  <td className="td whitespace-nowrap">
                    v{c.base_version} to {c.candidate_version ? `v${c.candidate_version}` : "no candidate"}
                    <div className="text-xs text-neutral-500">{fmtTime(c.created_at)}</div>
                  </td>
                  <td className="td">
                    <StatusPill status={c.status} />
                  </td>
                  <td className="td">{c.eval_runs}</td>
                  <td className="td">{fmtUsd(c.eval_cost_usd)}</td>
                  <td className="td">{fmtUsd(c.judge_cost_usd)}</td>
                  <td className="td">{fmtUsd(c.optimizer_cost_usd)}</td>
                  <td className="td font-medium">
                    {fmtUsd(c.total_cost_usd)}
                    {!c.cost_complete && <div className="text-[11px] font-normal text-amber-700 dark:text-amber-400">some prices unknown</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section
        title="Failures"
        actions={
          m.failures.versions.length > 0 && (
            <select
              aria-label="Failures for version"
              className="input w-auto py-1 text-xs"
              value={String(failVersion)}
              onChange={(e) => setFailVersion(e.target.value === "all" ? "all" : Number(e.target.value))}
            >
              <option value="all">All versions</option>
              {m.failures.versions.map((v) => (
                <option key={v.version} value={v.version}>
                  v{v.version}
                </option>
              ))}
            </select>
          )
        }
      >
        {failures.runs === 0 ? (
          <div className="card p-5 text-sm text-neutral-500">No failed runs, tool errors or negative feedback in this period.</div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2" data-testid="failure-breakdown">
            <div className="card min-w-0 p-4">
              <h3 className="mb-3 text-sm font-semibold">Top categories</h3>
              <RankBars
                testId="failure-categories"
                rows={failures.categories.map((c) => ({
                  key: c.category,
                  value: c.runs,
                  label: c.label,
                  detail: (
                    <span>
                      {c.category === "tool_errors" && `${c.count} failed tool calls · `}
                      Examples: <RunLinks ids={c.example_run_ids} />
                    </span>
                  ),
                }))}
              />
            </div>
            <div className="card min-w-0 p-4">
              <h3 className="mb-3 text-sm font-semibold">Top reasons</h3>
              <ul className="divide-y divide-neutral-100 text-sm dark:divide-neutral-800" data-testid="failure-reasons">
                {failures.reasons.map((r) => (
                  <li key={`${r.category}-${r.reason}`} className="py-2 first:pt-0">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="min-w-0 break-words">{r.reason}</span>
                      <span className="shrink-0 tabular-nums text-neutral-600 dark:text-neutral-400">{r.runs}</span>
                    </div>
                    <div className="mt-0.5 text-xs text-neutral-500">
                      {r.label} · <RunLinks ids={r.example_run_ids} />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </Section>

      <Section title="Tools">
        <div className="card overflow-x-auto" data-testid="tool-stats">
          <table className="w-full min-w-[560px] tabular-nums">
            <thead className="border-b border-neutral-200 dark:border-neutral-800">
              <tr>
                <th className="th">Tool</th>
                <th className="th">Calls</th>
                <th className="th">Error rate</th>
                <th className="th">p50</th>
                <th className="th">p95</th>
                <th className="th">Total time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
              {m.tools.tools.length === 0 && (
                <tr>
                  <td className="td text-neutral-500" colSpan={6}>No tool calls in this period yet</td>
                </tr>
              )}
              {m.tools.tools.map((t) => (
                <tr key={t.name} data-testid="tool-row" data-tool={t.name}>
                  <td className="td font-mono">{t.name}</td>
                  <td className="td">{t.calls}</td>
                  <td className="td">
                    {fmtPct(t.error_rate, 0)} <span className="text-neutral-400">({t.errors})</span>
                  </td>
                  <td className="td">{fmtMs(t.p50_ms)}</td>
                  <td className="td">{fmtMs(t.p95_ms)}</td>
                  <td className="td">{fmtMs(t.total_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {m.tools.slowest.length > 0 && (
          <div className="card min-w-0 p-4">
            <h3 className="mb-2 text-sm font-semibold">Slowest tool calls</h3>
            <ul className="divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
              {m.tools.slowest.map((c) => (
                <li key={`${c.run_id}-${c.seq}`} className="flex min-w-0 items-baseline gap-3 py-1.5">
                  <span className="w-16 shrink-0 tabular-nums">{fmtMs(c.duration_ms)}</span>
                  <span className="shrink-0 font-mono">{c.name}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-neutral-500">{c.summary}</span>
                  {c.is_error && <Badge tone="red">error</Badge>}
                  <Link to={`/runs/${c.run_id}`} className="shrink-0 font-mono text-xs hover:underline">
                    {c.run_id.slice(0, 8)}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      <Section title="Slowest runs">
        {m.slowest_runs.length === 0 ? (
          <div className="card p-5 text-sm text-neutral-500">No runs in this period yet</div>
        ) : (
          <div className="card overflow-x-auto" data-testid="slowest-runs">
            <table className="w-full min-w-[560px] tabular-nums">
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {m.slowest_runs.map((r) => (
                  <tr key={r.id}>
                    <td className="td w-20 whitespace-nowrap font-medium">{fmtMs(r.duration_ms)}</td>
                    <td className="td w-24">
                      <StatusPill status={r.status} />
                    </td>
                    <td className="td w-12">v{r.version}</td>
                    <td className="td max-w-[320px]">
                      <Link to={`/runs/${r.id}`} className="line-clamp-1 hover:underline">
                        {r.input}
                      </Link>
                    </td>
                    <td className="td whitespace-nowrap text-neutral-500">{fmtUsd(r.cost_usd)}</td>
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
