import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, type Overview } from "../api";
import { ChartCard, EmptyChart, Legend, RankBars, Sparkline, StackedBarChart, STATUS_KEYS, SWATCH_BG, useTick } from "../components/charts/Charts";
import { KpiRow, PeriodSelector } from "../components/Kpis";
import { Empty, ErrorBox, Loading } from "../components/ui";
import { fmtAgo, fmtMs, fmtPct, fmtUsd } from "../lib/format";
import { periodLabel, tzOffsetMin, usePeriod } from "../lib/period";
import { bucketLabel } from "../lib/scale";

export function SeriesTable({ series, bucket, cost }: { series: Overview["series"]; bucket: "hour" | "day"; cost?: boolean }) {
  return (
    <table className="w-full text-left tabular-nums">
      <thead>
        <tr className="text-neutral-500">
          <th className="py-1 pr-3 font-medium">{bucket === "hour" ? "Hour" : "Day"}</th>
          {cost ? (
            <th className="py-1 pr-3 font-medium">Spend</th>
          ) : (
            <>
              <th className="py-1 pr-3 font-medium">Runs</th>
              <th className="py-1 pr-3 font-medium">Succeeded</th>
              <th className="py-1 pr-3 font-medium">Failed</th>
              <th className="py-1 pr-3 font-medium">Timeout</th>
            </>
          )}
        </tr>
      </thead>
      <tbody>
        {series.map((b) => (
          <tr key={b.t} className="border-t border-neutral-100 dark:border-neutral-800">
            <td className="py-1 pr-3">{bucketLabel(b.t, bucket)}</td>
            {cost ? (
              <td className="py-1 pr-3">{fmtUsd(b.cost_usd)}</td>
            ) : (
              <>
                <td className="py-1 pr-3">{b.runs}</td>
                <td className="py-1 pr-3">{b.succeeded}</td>
                <td className="py-1 pr-3">{b.failed}</td>
                <td className="py-1 pr-3">{b.timeout}</td>
              </>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export const bucketSeconds = (bucket: "hour" | "day") => (bucket === "hour" ? 3600 : 86400);

export default function DashboardPage() {
  const [days, setDays] = usePeriod();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useTick(60_000);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .overview(days, tzOffsetMin())
        .then((d) => alive && (setData(d), setError(null)))
        .catch((e) => alive && setError(errMsg(e)));
    load();
    const t = setInterval(load, 15_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [days]);

  const label = periodLabel(days);
  const empty = data !== null && data.summary.runs === 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">Production usage across all agents. Evaluation runs are excluded.</p>
        </div>
        <PeriodSelector days={days} onChange={setDays} />
      </div>
      <ErrorBox error={error} />
      {!data && !error && <Loading label="Loading dashboard" />}
      {data && (
        <>
          <KpiRow
            kpis={data.kpis}
            periodLabel={label}
            notes={data.summary.unpriced_runs ? { cost_usd: `${data.summary.unpriced_runs} runs with unknown price` } : {}}
          />
          <div className="grid gap-4 lg:grid-cols-2">
            <ChartCard
              title={`Runs per ${data.bucket}`}
              subtitle="By final status"
              legend={<Legend keys={STATUS_KEYS.slice(0, 3).map((k) => ({ label: k.label, swatch: SWATCH_BG[k.key] }))} />}
              table={<SeriesTable series={data.series} bucket={data.bucket} />}
              testId="chart-runs"
            >
              {empty ? (
                <EmptyChart />
              ) : (
                <StackedBarChart points={data.series as never} keys={STATUS_KEYS} bucket={data.bucket} bucketSize={bucketSeconds(data.bucket)} label="Runs per day by status" />
              )}
            </ChartCard>
            <ChartCard
              title={`Spend per ${data.bucket}`}
              subtitle="Estimated model spend in USD. Self-hosted models are not metered."
              table={<SeriesTable series={data.series} bucket={data.bucket} cost />}
              testId="chart-spend"
            >
              {empty ? (
                <EmptyChart />
              ) : (
                <StackedBarChart
                  points={data.series as never}
                  keys={[{ key: "cost_usd", label: "Spend", fill: "fill-accent-500" }]}
                  bucket={data.bucket}
                  bucketSize={bucketSeconds(data.bucket)}
                  integer={false}
                  yFormat={(v) => fmtUsd(v).replace("$0.0000", "$0")}
                  tooltipFormat={fmtUsd}
                  label="Spend per day"
                />
              )}
            </ChartCard>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <section className="card min-w-0 lg:col-span-2" aria-label="Agents">
              <h3 className="border-b border-neutral-200 px-4 py-3 text-sm font-semibold dark:border-neutral-800">Agents</h3>
              {data.agents.length === 0 ? (
                <div className="p-4">
                  <Empty>No agents yet.</Empty>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[680px] tabular-nums">
                    <thead className="border-b border-neutral-200 dark:border-neutral-800">
                      <tr>
                        <th className="th">Agent</th>
                        <th className="th">Runs</th>
                        <th className="th">Trend</th>
                        <th className="th">Success</th>
                        <th className="th">p95</th>
                        <th className="th">Spend</th>
                        <th className="th">Last run</th>
                        <th className="th">Failing</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                      {data.agents.map((a) => (
                        <tr key={a.slug} data-testid="dashboard-agent" data-slug={a.slug}>
                          <td className="td">
                            <Link to={`/agents/${a.slug}/monitoring`} className="whitespace-nowrap font-medium hover:underline">
                              {a.name}
                            </Link>
                            <div className="whitespace-nowrap font-mono text-xs text-neutral-500">{a.slug}</div>
                          </td>
                          <td className="td">{a.health.runs}</td>
                          <td className="td">
                            <Sparkline values={a.spark} label={`Runs per ${data.bucket} for ${a.name}`} />
                          </td>
                          <td className="td">{fmtPct(a.health.success_rate, 0)}</td>
                          <td className="td whitespace-nowrap">{fmtMs(a.health.p95_ms)}</td>
                          <td className="td whitespace-nowrap">
                            {fmtUsd(a.health.cost_usd)}
                            {a.health.unpriced_runs > 0 && <div className="text-xs text-amber-700 dark:text-amber-400">unknown price</div>}
                          </td>
                          <td className="td whitespace-nowrap text-neutral-500">{fmtAgo(a.last_run_at)}</td>
                          <td className="td">
                            {a.failing_runs > 0 ? (
                              <Link to={`/agents/${a.slug}/runs?status=failed,timeout`} className="font-medium text-red-700 hover:underline dark:text-red-400">
                                {a.failing_runs}
                              </Link>
                            ) : (
                              <span className="text-neutral-400">0</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="card min-w-0 p-4" aria-label="Top failure reasons">
              <h3 className="mb-3 text-sm font-semibold">Top failure reasons</h3>
              {data.top_failure_reasons.length === 0 ? (
                <p className="text-sm text-neutral-500">No failures or negative feedback in this period.</p>
              ) : (
                <RankBars
                  testId="top-failure-reasons"
                  rows={data.top_failure_reasons.map((r) => ({
                    key: `${r.agent}-${r.category}-${r.reason}`,
                    value: r.runs,
                    label: (
                      <span>
                        <span className="text-xs font-medium uppercase tracking-wide text-neutral-500">{r.label}</span>
                        <span className="block break-words">{r.reason}</span>
                      </span>
                    ),
                    detail: (
                      <span>
                        <Link to={`/agents/${r.agent}/monitoring`} className="hover:underline">
                          {r.agent_name}
                        </Link>
                        {r.example_run_ids.length > 0 && (
                          <>
                            {" · "}
                            {r.example_run_ids.map((id, i) => (
                              <span key={id}>
                                {i > 0 && ", "}
                                <Link to={`/runs/${id}`} className="font-mono hover:underline">
                                  {id.slice(0, 8)}
                                </Link>
                              </span>
                            ))}
                          </>
                        )}
                      </span>
                    ),
                  }))}
                />
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
