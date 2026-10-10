import type { ForgeEvent } from "../../api";

/** Pure helpers behind the transcript view: grouping events, parsing tool output, line diffs. */

export type TranscriptItem =
  | { kind: "narration"; seq: number; text: string; final: boolean }
  | { kind: "tool"; seq: number; call: ForgeEvent; result?: ForgeEvent }
  | { kind: "error"; seq: number; message: string }
  | { kind: "orphan"; seq: number; ev: ForgeEvent };

export function str(v: unknown): string {
  if (v === undefined || v === null) return "";
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

export function buildItems(events: ForgeEvent[]): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  const byId = new Map<string, Extract<TranscriptItem, { kind: "tool" }>>();
  // An assistant event without `kind` (older traces) is final when nothing but the end follows it.
  const lastToolSeq = events.reduce((m, e) => (e.type === "tool_call" ? Math.max(m, e.seq) : m), -1);
  for (const ev of events) {
    const p = ev.payload;
    if (ev.type === "assistant") {
      const text = str(p.text).trim();
      if (!text) continue;
      const final = p.kind ? p.kind === "final" : ev.seq > lastToolSeq;
      items.push({ kind: "narration", seq: ev.seq, text, final });
    } else if (ev.type === "tool_call") {
      const item = { kind: "tool" as const, seq: ev.seq, call: ev };
      byId.set(str(p.id), item);
      items.push(item);
    } else if (ev.type === "tool_result") {
      const item = byId.get(str(p.id));
      if (item && !item.result) item.result = ev;
      else items.push({ kind: "orphan", seq: ev.seq, ev });
    } else if (ev.type === "error") {
      items.push({ kind: "error", seq: ev.seq, message: str(p.message) });
    }
  }
  return items;
}

export function splitLines(text: string): string[] {
  const t = text.replace(/\n$/, "");
  return t ? t.split("\n") : [];
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// ---------------------------------------------------------------- bash

export interface BashOutput {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

const BASH_RE = /^exit_code: (-?\d+)\n--- stdout ---\n([\s\S]*?)\n--- stderr ---\n([\s\S]*)$/;
const TIMED_OUT_RE = /\n?\[command timed out after (\d+)s and was killed\]\s*$/;

/** forge/loop.py formats bash results as `exit_code: N`, then stdout and stderr sections. */
export function parseBash(output: string): BashOutput {
  const timedOut = TIMED_OUT_RE.test(output);
  const body = output.replace(TIMED_OUT_RE, "");
  const m = BASH_RE.exec(body);
  if (!m) return { exitCode: null, stdout: body, stderr: "", timedOut };
  return { exitCode: Number(m[1]), stdout: m[2], stderr: m[3], timedOut };
}

export function bashCommand(args: Record<string, unknown>): string {
  if (typeof args.cmd === "string") return args.cmd;
  if (typeof args.command === "string") return args.command;
  return "";
}

// ---------------------------------------------------------------- labels

export function clip(s: string, n = 80): string {
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

/** Claude Code style header: `Bash(cmd)`, `Read(path:1-20)`, `Write(path)`, `Update(path)`. */
export function toolLabel(name: string, args: Record<string, unknown>): { tool: string; arg: string } {
  const path = str(args.path);
  switch (name) {
    case "bash": {
      const lines = bashCommand(args).trim().split("\n");
      return { tool: "Bash", arg: clip(lines[0] + (lines.length > 1 ? " …" : "")) };
    }
    case "read_file": {
      const start = typeof args.start === "number" ? args.start : undefined;
      const end = typeof args.end === "number" ? args.end : undefined;
      const range = start || end ? `:${start ?? 1}${end ? `-${end}` : ""}` : "";
      return { tool: "Read", arg: path + range };
    }
    case "write_file":
      return { tool: "Write", arg: path };
    case "edit_file":
      return { tool: "Update", arg: path };
    default:
      return { tool: name, arg: clip(JSON.stringify(args), 60) };
  }
}

/** read_file output lines look like `  12\tcontent`. */
export function parseNumbered(output: string): { n: string; text: string }[] {
  if (output.trim() === "(empty file)") return [];
  return splitLines(output).map((line) => {
    const m = /^\s*(\d+)\t(.*)$/.exec(line);
    return m ? { n: m[1], text: m[2] } : { n: "", text: line };
  });
}

// ---------------------------------------------------------------- diff

export interface DiffLine {
  kind: "same" | "add" | "del";
  text: string;
  oldN?: number;
  newN?: number;
}

const MAX_LCS = 4_000_000; // cells; above this fall back to a plain remove-then-add diff

/** Line diff via LCS; trims a shared prefix and suffix first so typical edits stay cheap. */
export function lineDiff(oldText: string, newText: string): DiffLine[] {
  const a = splitLines(oldText);
  const b = splitLines(newText);
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const am = a.slice(pre, a.length - suf);
  const bm = b.slice(pre, b.length - suf);
  const mid: DiffLine[] = [];
  if (am.length * bm.length > MAX_LCS) {
    am.forEach((t) => mid.push({ kind: "del", text: t }));
    bm.forEach((t) => mid.push({ kind: "add", text: t }));
  } else {
    const n = am.length;
    const m = bm.length;
    const dp: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--)
        dp[i][j] = am[i] === bm[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    let i = 0;
    let j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && am[i] === bm[j]) {
        mid.push({ kind: "same", text: am[i] });
        i++;
        j++;
      } else if (i < n && (j >= m || dp[i + 1][j] >= dp[i][j + 1])) {
        mid.push({ kind: "del", text: am[i++] }); // removals before additions, like git
      } else {
        mid.push({ kind: "add", text: bm[j++] });
      }
    }
  }
  const out: DiffLine[] = [
    ...a.slice(0, pre).map((t) => ({ kind: "same" as const, text: t })),
    ...mid,
    ...a.slice(a.length - suf).map((t) => ({ kind: "same" as const, text: t })),
  ];
  let o = 1;
  let nn = 1;
  for (const l of out) {
    if (l.kind !== "add") l.oldN = o++;
    if (l.kind !== "del") l.newN = nn++;
  }
  return out;
}

export function diffStats(lines: DiffLine[]): { added: number; removed: number } {
  return {
    added: lines.filter((l) => l.kind === "add").length,
    removed: lines.filter((l) => l.kind === "del").length,
  };
}

/** Keep changed lines plus `context` lines around them; gaps become null separators. */
export function withContext(lines: DiffLine[], context = 3): (DiffLine | null)[] {
  const keep = new Array(lines.length).fill(false);
  lines.forEach((l, i) => {
    if (l.kind === "same") return;
    for (let k = Math.max(0, i - context); k <= Math.min(lines.length - 1, i + context); k++) keep[k] = true;
  });
  const out: (DiffLine | null)[] = [];
  let gap = false;
  lines.forEach((l, i) => {
    if (keep[i]) {
      if (gap) out.push(null);
      gap = false;
      out.push(l);
    } else gap = true;
  });
  if (gap && out.length) out.push(null);
  return out;
}

// ---------------------------------------------------------------- stats

/** Final durations: one decimal under a minute ("16.5s"). */
export function fmtSeconds(s: number): string {
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

/** Live elapsed time: whole seconds ("12s", "1m 5s"). */
export function fmtElapsed(s: number): string {
  const t = Math.floor(s);
  return t < 60 ? `${t}s` : `${Math.floor(t / 60)}m ${t % 60}s`;
}

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

/** Tokens so far: per-event token fields when the backend records them, else the run's totals. */
export function tokensFromEvents(events: ForgeEvent[]): number {
  let total = 0;
  for (const e of events) {
    if (e.type === "run_finished") return num(e.payload.tokens_in) + num(e.payload.tokens_out);
    const p = e.payload;
    total += num(p.tokens_in) + num(p.tokens_out);
    if (typeof p.tokens === "number") total += p.tokens;
    else if (p.tokens && typeof p.tokens === "object") {
      const t = p.tokens as Record<string, unknown>;
      total += num(t.in) + num(t.out) + num(t.input) + num(t.output);
    }
  }
  return total;
}

/** Cost in USD if anything recorded it (run_finished payload or the run itself); otherwise null. */
export function costFrom(events: ForgeEvent[], run?: Record<string, unknown> | null): number | null {
  const fin = events.find((e) => e.type === "run_finished")?.payload ?? {};
  for (const v of [fin.cost_usd, fin.cost, run?.cost_usd, run?.cost]) if (typeof v === "number") return v;
  return null;
}

export function fmtCost(c: number): string {
  return c < 0.01 ? `$${c.toFixed(4)}` : `$${c.toFixed(2)}`;
}
