import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { CalendarClock, Github, MessageSquare, Play, Trash2 } from "lucide-react";
import { errMsg, fmtTime, type Agent } from "../../api";
import StatusPill from "../../components/StatusPill";
import { Badge, CopyButton, ErrorBox, Section, Toggle } from "../../components/ui";
import { describeCron, formatFireTime, localTimeZone, nextTimes, PRESETS, validTimeZone } from "../../lib/cron";
import { hookUrl, triggersApi, type GithubEvent, type Trigger, type TriggerFire } from "../../triggersApi";

const GITHUB_EVENTS: { key: GithubEvent; label: string }[] = [
  { key: "issues", label: "Issues opened (or labeled)" },
  { key: "issue_comment", label: "Comments starting with the command" },
  { key: "pull_request", label: "Pull requests opened or updated" },
  { key: "push", label: "Pushes" },
];

function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="label">
        {label}
      </label>
      {children(id)}
      {hint && <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">{hint}</p>}
    </div>
  );
}

function UrlRow({ url, label }: { url: string; label: string }) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium text-neutral-500 dark:text-neutral-400">{label}</div>
      <div className="flex flex-wrap items-center gap-2">
        <code
          aria-label={label}
          className="min-w-0 flex-1 break-all rounded-md bg-neutral-100 px-2.5 py-1.5 font-mono text-xs dark:bg-neutral-950"
        >
          {url}
        </code>
        <CopyButton text={url} />
      </div>
    </div>
  );
}

function SecretOnce({ secret, onDismiss }: { secret: string; onDismiss: () => void }) {
  return (
    <div role="region" aria-label="Webhook secret" className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/40">
      <p className="text-sm font-medium text-amber-900 dark:text-amber-200">Copy the webhook secret now. It is shown only once.</p>
      <div className="flex flex-wrap items-center gap-2">
        <code data-testid="webhook-secret" className="min-w-0 flex-1 break-all rounded-md bg-white px-2.5 py-1.5 font-mono text-xs dark:bg-neutral-950">
          {secret}
        </code>
        <CopyButton text={secret} />
        <button className="btn-ghost btn-sm" onClick={onDismiss}>
          Done
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- schedule

export function SchedulePreview({ cron, timezone, now = Date.now() }: { cron: string; timezone: string; now?: number }) {
  const preview = useMemo(() => {
    if (!validTimeZone(timezone)) return { error: `Unknown time zone "${timezone}"` };
    try {
      return { times: nextTimes(cron, timezone, now, 3) };
    } catch (e) {
      return { error: errMsg(e) };
    }
  }, [cron, timezone, now]);
  if ("error" in preview) return <p className="text-sm text-red-600 dark:text-red-400">{preview.error}</p>;
  return (
    <div aria-label="Schedule preview" role="region" className="rounded-lg bg-neutral-50 p-3 text-sm dark:bg-neutral-950">
      <div className="font-medium">{describeCron(cron)}</div>
      <div className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">Next runs</div>
      <ul className="mt-1 space-y-0.5 text-neutral-700 dark:text-neutral-300">
        {preview.times!.map((t) => (
          <li key={t}>{formatFireTime(t, timezone)}</li>
        ))}
      </ul>
    </div>
  );
}

function ScheduleForm({ slug, onDone }: { slug: string; onDone: (t: Trigger) => void }) {
  const [cron, setCron] = useState(PRESETS[0].cron);
  const [timezone, setTimezone] = useState(localTimeZone());
  const [template, setTemplate] = useState("Write the daily status report for {date}.");
  const [skip, setSkip] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const preset = PRESETS.find((p) => p.cron === cron)?.cron ?? "custom";

  async function save() {
    setBusy(true);
    setError(null);
    try {
      onDone(
        await triggersApi.create(slug, {
          kind: "schedule",
          config: { cron, timezone, input_template: template, skip_if_running: skip },
        }),
      );
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Preset">
          {(id) => (
            <select id={id} className="input" value={preset} onChange={(e) => e.target.value !== "custom" && setCron(e.target.value)}>
              {PRESETS.map((p) => (
                <option key={p.cron} value={p.cron}>
                  {p.label}
                </option>
              ))}
              <option value="custom">Custom</option>
            </select>
          )}
        </Field>
        <Field label="Cron expression" hint="minute hour day-of-month month day-of-week">
          {(id) => <input id={id} className="input font-mono" value={cron} onChange={(e) => setCron(e.target.value)} />}
        </Field>
        <Field label="Time zone" hint="An IANA name such as America/New_York">
          {(id) => <input id={id} className="input" value={timezone} onChange={(e) => setTimezone(e.target.value)} />}
        </Field>
        <SchedulePreview cron={cron} timezone={timezone} />
      </div>
      <Field label="Input" hint="Sent to the agent each time. {date}, {time} and {weekday} are filled in.">
        {(id) => <textarea id={id} className="input min-h-[72px]" value={template} onChange={(e) => setTemplate(e.target.value)} />}
      </Field>
      <Toggle checked={skip} onChange={setSkip} label="Skip a run while the previous one is still running" />
      <ErrorBox error={error} />
      <button className="btn-primary" onClick={save} disabled={busy || !template.trim()}>
        {busy ? "Saving..." : "Save schedule"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- GitHub

function GithubForm({ slug, onDone }: { slug: string; onDone: (t: Trigger, secret?: string) => void }) {
  const [events, setEvents] = useState<GithubEvent[]>(["issues", "issue_comment", "pull_request"]);
  const [label, setLabel] = useState("");
  const [command, setCommand] = useState("/forge");
  const [branches, setBranches] = useState("");
  const [template, setTemplate] = useState("");
  const [token, setToken] = useState("");
  const [commentBack, setCommentBack] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(e: GithubEvent) {
    setEvents((cur) => (cur.includes(e) ? cur.filter((x) => x !== e) : [...cur, e]));
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const t = await triggersApi.create(slug, {
        kind: "github",
        token: token.trim() || undefined,
        config: {
          events,
          label: label.trim(),
          command: command.trim(),
          branches: branches.split(",").map((b) => b.trim()).filter(Boolean),
          input_template: template,
          comment_back: commentBack,
        },
      });
      onDone(t, t.secret);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="label">Events</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {GITHUB_EVENTS.map((e) => (
            <label key={e.key} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={events.includes(e.key)} onChange={() => toggle(e.key)} />
              {e.label}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Label filter" hint="Only issues given this label. Empty: every new issue.">
          {(id) => <input id={id} className="input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="forge" />}
        </Field>
        <Field label="Comment command" hint="Comments must start with it.">
          {(id) => <input id={id} className="input font-mono" value={command} onChange={(e) => setCommand(e.target.value)} />}
        </Field>
        <Field label="Push branches" hint="Comma separated, * wildcards. Empty: all.">
          {(id) => <input id={id} className="input font-mono" value={branches} onChange={(e) => setBranches(e.target.value)} placeholder="main" />}
        </Field>
      </div>
      <Field
        label="Input template"
        hint="Optional. Fields: {repo} {title} {body} {comment} {pr_url} {url} {number} {sender} {branch} {commits}"
      >
        {(id) => (
          <textarea
            id={id}
            className="input min-h-[72px] font-mono text-xs"
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
            placeholder="Fix GitHub issue {repo}#{number}: {title}&#10;&#10;{body}"
          />
        )}
      </Field>
      <Field label="GitHub token" hint="Optional. Lets Forge post the result as a comment. Needs write access to issues and pull requests.">
        {(id) => <input id={id} type="password" className="input" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />}
      </Field>
      <Toggle checked={commentBack} onChange={setCommentBack} label="Post the result back as a comment" hint="Only when a token is set." />
      <ErrorBox error={error} />
      <button className="btn-primary" onClick={save} disabled={busy || events.length === 0}>
        {busy ? "Saving..." : "Save GitHub trigger"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- Slack

function SlackForm({ slug, onDone }: { slug: string; onDone: (t: Trigger) => void }) {
  const [secret, setSecret] = useState("");
  const [token, setToken] = useState("");
  const [template, setTemplate] = useState("{text}");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      onDone(
        await triggersApi.create(slug, {
          kind: "slack",
          signing_secret: secret.trim() || undefined,
          token: token.trim() || undefined,
          config: { input_template: template },
        }),
      );
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Signing secret" hint="Slack app settings, Basic Information, App Credentials. You can add it later.">
          {(id) => <input id={id} type="password" className="input" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" />}
        </Field>
        <Field label="Bot token" hint="Optional, starts with xoxb. Needed to answer @mentions in a thread.">
          {(id) => <input id={id} type="password" className="input" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />}
        </Field>
      </div>
      <Field label="Input template" hint="{text} is what the user typed. Also {user} and {channel}.">
        {(id) => <input id={id} className="input font-mono" value={template} onChange={(e) => setTemplate(e.target.value)} />}
      </Field>
      <ErrorBox error={error} />
      <button className="btn-primary" onClick={save} disabled={busy}>
        {busy ? "Saving..." : "Save Slack trigger"}
      </button>
    </div>
  );
}

function SlackChecklist() {
  return (
    <ol className="list-decimal space-y-1 pl-5 text-sm text-neutral-600 dark:text-neutral-400">
      <li>Create a Slack app at api.slack.com/apps (From scratch).</li>
      <li>Slash Commands: create a command such as /forge with the request URL above.</li>
      <li>Optional, for @mentions: Event Subscriptions on, same request URL, subscribe to the app_mention bot event.</li>
      <li>OAuth and Permissions: add the commands scope (and app_mentions:read plus chat:write for mentions), then install.</li>
      <li>Paste the signing secret here, and the bot token if you use mentions.</li>
    </ol>
  );
}

// ---------------------------------------------------------------- list

function summary(t: Trigger): string {
  const c = t.config;
  if (t.kind === "schedule") return `${describeCron(c.cron || "")} (${c.cron}, ${c.timezone || "UTC"})`;
  if (t.kind === "github") {
    const parts = [(c.events || []).join(", ")];
    if (c.label) parts.push(`label ${c.label}`);
    if ((c.events || []).includes("issue_comment")) parts.push(`command ${c.command}`);
    if (c.branches && c.branches.length) parts.push(`branches ${c.branches.join(", ")}`);
    return parts.join(" · ");
  }
  return `Slash command and mentions, input ${c.input_template || "{text}"}`;
}

const KIND_LABEL = { schedule: "Schedule", github: "GitHub", slack: "Slack" } as const;
const KIND_ICON = { schedule: CalendarClock, github: Github, slack: MessageSquare } as const;

function FireRow({ f }: { f: TriggerFire }) {
  return (
    <li className="flex flex-wrap items-center gap-2 py-1.5 text-sm">
      <span className="w-28 shrink-0 text-xs text-neutral-500 dark:text-neutral-400">{fmtTime(f.created_at)}</span>
      {f.run ? (
        <>
          <StatusPill status={f.run.status} />
          <Link to={`/runs/${f.run.id}`} className="font-mono text-xs text-accent-700 hover:underline dark:text-accent-300">
            {f.run.id}
          </Link>
        </>
      ) : (
        <Badge tone={f.status === "error" || f.status === "rate_limited" ? "red" : "amber"}>{f.status.replace("_", " ")}</Badge>
      )}
      {f.test && <Badge>test</Badge>}
      {f.delivery === "failed" && <Badge tone="red">reply failed</Badge>}
      {f.delivery === "delivered" && <Badge tone="green">replied</Badge>}
      {f.detail && <span className="min-w-0 flex-1 truncate text-xs text-neutral-500 dark:text-neutral-400">{f.detail}</span>}
    </li>
  );
}

function TriggerCard({
  t,
  secret,
  onSecretSeen,
  onChanged,
  onRemoved,
}: {
  t: Trigger;
  secret?: string;
  onSecretSeen: () => void;
  onChanged: (t: Trigger) => void;
  onRemoved: () => void;
}) {
  const [history, setHistory] = useState<TriggerFire[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [signing, setSigning] = useState("");
  const Icon = KIND_ICON[t.kind];
  const url = t.hook_path ? hookUrl(t, window.location.origin) : null;

  const load = useCallback(async () => {
    try {
      setHistory(await triggersApi.history(t.id, 10));
    } catch (e) {
      setError(errMsg(e));
    }
  }, [t.id]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card space-y-4 p-5" role="region" aria-label={`${KIND_LABEL[t.kind]} trigger ${t.id}`} data-testid="trigger-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Icon className="h-4 w-4 text-neutral-500" />
            <span className="font-medium">{KIND_LABEL[t.kind]}</span>
            {!t.enabled && <Badge>off</Badge>}
            {t.config.version ? <Badge tone="violet">pinned v{t.config.version}</Badge> : <Badge tone="green">prod</Badge>}
          </div>
          <p className="mt-1 break-words text-sm text-neutral-600 dark:text-neutral-400">{summary(t)}</p>
        </div>
        <Toggle
          checked={t.enabled}
          label="Enabled"
          onChange={(v) => act(async () => onChanged(await triggersApi.patch(t.id, { enabled: v })))}
        />
      </div>

      {secret && <SecretOnce secret={secret} onDismiss={onSecretSeen} />}
      {url && <UrlRow url={url} label={t.kind === "github" ? "Payload URL" : "Request URL"} />}
      {t.kind === "github" && (
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          In the repository: Settings, Webhooks, Add webhook. Content type application/json, the secret above, and the events you chose.
          {!t.has_token && " Add a token to get results as comments."}
        </p>
      )}
      {t.kind === "slack" && (
        <>
          <SlackChecklist />
          {!t.has_secret && (
            <p className="text-sm text-amber-700 dark:text-amber-300">Slack requests are rejected until the signing secret is saved.</p>
          )}
          {!t.has_secret && (
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-0 flex-1">
                <label className="label" htmlFor={`sig-${t.id}`}>
                  Signing secret
                </label>
                <input
                  id={`sig-${t.id}`}
                  type="password"
                  className="input"
                  value={signing}
                  onChange={(e) => setSigning(e.target.value)}
                  autoComplete="off"
                />
              </div>
              <button
                className="btn-secondary"
                disabled={busy || !signing.trim()}
                onClick={() => act(async () => onChanged(await triggersApi.patch(t.id, { signing_secret: signing.trim() })))}
              >
                Save secret
              </button>
            </div>
          )}
        </>
      )}

      <dl className="grid gap-2 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-neutral-500 dark:text-neutral-400">Last fired</dt>
          <dd data-testid="last-fired">{t.last_fired_at ? fmtTime(t.last_fired_at) : "Never"}</dd>
        </div>
        {t.kind === "schedule" && (
          <div className="sm:col-span-2">
            <dt className="text-xs text-neutral-500 dark:text-neutral-400">Next</dt>
            <dd>
              {t.next_fire_times && t.next_fire_times.length
                ? t.next_fire_times.map((s) => (
                    <span key={s} className="mr-3 inline-block whitespace-nowrap">
                      {formatFireTime(s * 1000, t.config.timezone || "UTC")}
                    </span>
                  ))
                : "Not scheduled"}
            </dd>
          </div>
        )}
      </dl>
      {t.last_error && (
        <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800 dark:bg-red-950/50 dark:text-red-300">
          Last error: {t.last_error}
        </div>
      )}
      <ErrorBox error={error} onDismiss={() => setError(null)} />

      <div className="flex flex-wrap gap-2">
        <button
          className="btn-secondary btn-sm"
          disabled={busy}
          onClick={() =>
            act(async () => {
              await triggersApi.test(t.id);
              await load();
            })
          }
        >
          <Play className="h-3.5 w-3.5" /> Test
        </button>
        <button
          className="btn-ghost btn-sm text-red-600"
          disabled={busy}
          onClick={() => {
            if (confirm("Delete this trigger?")) act(async () => {
              await triggersApi.remove(t.id);
              onRemoved();
            });
          }}
        >
          <Trash2 className="h-3.5 w-3.5" /> Delete
        </button>
      </div>

      <div role="region" aria-label="Recent runs">
        <div className="text-xs font-medium uppercase tracking-wide text-neutral-500 dark:text-neutral-400">Recent runs</div>
        {history && history.length === 0 && <p className="py-1.5 text-sm text-neutral-500">Nothing yet.</p>}
        <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
          {(history || []).map((f) => (
            <FireRow key={f.id} f={f} />
          ))}
        </ul>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- section

type Adding = "schedule" | "github" | "slack" | null;

export default function TriggersSection({ agent }: { agent: Agent }) {
  const [triggers, setTriggers] = useState<Trigger[] | null>(null);
  const [adding, setAdding] = useState<Adding>(null);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setTriggers(await triggersApi.list(agent.slug));
    } catch (e) {
      setError(errMsg(e));
    }
  }, [agent.slug]);

  useEffect(() => {
    reload();
    const timer = setInterval(reload, 10000);
    return () => clearInterval(timer);
  }, [reload]);

  function added(t: Trigger, secret?: string) {
    setAdding(null);
    if (secret) setSecrets((s) => ({ ...s, [t.id]: secret }));
    setTriggers((cur) => [...(cur || []), t]);
  }

  return (
    <Section
      title="Triggers"
      actions={
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary btn-sm" onClick={() => setAdding("schedule")}>
            <CalendarClock className="h-3.5 w-3.5" /> Add schedule
          </button>
          <button className="btn-secondary btn-sm" onClick={() => setAdding("github")}>
            <Github className="h-3.5 w-3.5" /> Add GitHub
          </button>
          <button className="btn-secondary btn-sm" onClick={() => setAdding("slack")}>
            <MessageSquare className="h-3.5 w-3.5" /> Add Slack
          </button>
        </div>
      }
    >
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Start runs on a schedule, from GitHub events or from Slack. Triggered runs use the production version unless a trigger pins
        one.
      </p>
      <ErrorBox error={error} />
      {adding && (
        <div className="card space-y-4 p-5" role="region" aria-label={`New ${KIND_LABEL[adding]} trigger`}>
          <div className="flex items-center justify-between">
            <h3 className="font-medium">New {KIND_LABEL[adding]} trigger</h3>
            <button className="btn-ghost btn-sm" onClick={() => setAdding(null)}>
              Cancel
            </button>
          </div>
          {adding === "schedule" && <ScheduleForm slug={agent.slug} onDone={added} />}
          {adding === "github" && <GithubForm slug={agent.slug} onDone={added} />}
          {adding === "slack" && <SlackForm slug={agent.slug} onDone={added} />}
        </div>
      )}
      {triggers && triggers.length === 0 && !adding && (
        <div className="rounded-xl border border-dashed border-neutral-300 px-6 py-6 text-center text-sm text-neutral-500 dark:border-neutral-700 dark:text-neutral-400">
          No triggers yet.
        </div>
      )}
      <div className="space-y-4">
        {(triggers || []).map((t) => (
          <TriggerCard
            key={t.id}
            t={t}
            secret={secrets[t.id]}
            onSecretSeen={() => setSecrets(({ [t.id]: _gone, ...rest }) => rest)}
            onChanged={(nt) => setTriggers((cur) => (cur || []).map((x) => (x.id === nt.id ? nt : x)))}
            onRemoved={() => setTriggers((cur) => (cur || []).filter((x) => x.id !== t.id))}
          />
        ))}
      </div>
    </Section>
  );
}
