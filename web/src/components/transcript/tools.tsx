import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ForgeEvent } from "../../api";
import { Highlighted, langFromPath } from "./highlight";
import {
  bashCommand,
  diffStats,
  fmtElapsed,
  lineDiff,
  parseBash,
  parseNumbered,
  plural,
  splitLines,
  str,
  toolLabel,
  withContext,
  type DiffLine,
} from "./model";

/** Lines shown before "… +N lines". */
export const PREVIEW_LINES = 4;
/** Lines rendered when expanded before a "show all" step, so huge outputs stay cheap. */
export const RENDER_CAP = 400;
/** Diff lines shown inline before expanding. */
const DIFF_PREVIEW = 16;

/** Expand all / collapse all: every tool resets to `open` whenever `epoch` changes. */
export const ExpandContext = createContext<{ open: boolean; epoch: number }>({ open: false, epoch: 0 });

function useExpanded(): [boolean, (v: boolean) => void] {
  const { open: all, epoch } = useContext(ExpandContext);
  const [open, setOpen] = useState(all);
  useEffect(() => {
    if (epoch > 0) setOpen(all);
  }, [epoch, all]);
  return [open, setOpen];
}

export function Dot({ tone }: { tone: "text" | "ok" | "error" | "pending" }) {
  const cls = {
    text: "text-neutral-800 dark:text-neutral-200",
    ok: "text-emerald-600 dark:text-emerald-400",
    error: "text-red-600 dark:text-red-400",
    pending: "text-neutral-400 animate-pulse",
  }[tone];
  // The trailing space keeps copied text reading "● Tool(arg)"; flex layout does not render it.
  return (
    <>
      <span className={`tx-dot ${cls}`} aria-hidden="true">
        ●
      </span>{" "}
    </>
  );
}

export function Spinner() {
  return (
    <span
      className="inline-block h-3 w-3 animate-spin rounded-full border-[1.5px] border-neutral-300 border-t-neutral-600 align-[-2px] dark:border-neutral-700 dark:border-t-neutral-300"
      role="status"
      aria-label="Running"
    />
  );
}

/** The `⎿` result line(s) under a tool header. */
function Result({ children, tone = "dim" }: { children: React.ReactNode; tone?: "dim" | "error" | "plain" }) {
  const cls = {
    dim: "text-neutral-500 dark:text-neutral-400",
    error: "text-red-700 dark:text-red-400",
    plain: "text-neutral-700 dark:text-neutral-300",
  }[tone];
  return (
    <div className={`tx-result ${cls}`}>
      <span className="tx-elbow" aria-hidden="true">
        ⎿
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function MoreLines({ n, onClick, expanded }: { n: number; onClick: () => void; expanded?: boolean }) {
  if (n <= 0 && !expanded) return null;
  return (
    <button type="button" onClick={onClick} className="tx-more" data-testid="more-lines">
      {expanded ? "(click to collapse)" : `… +${plural(n, "line")} (click to expand)`}
    </button>
  );
}

/** Plain lines with a render cap; "Show all" lifts the cap for this block only. */
function Lines({ lines, className = "" }: { lines: string[]; className?: string }) {
  const [all, setAll] = useState(false);
  const shown = all ? lines : lines.slice(0, RENDER_CAP);
  return (
    <>
      <pre className={`tx-pre ${className}`}>{shown.join("\n")}</pre>
      {lines.length > shown.length && (
        <button type="button" className="tx-more" onClick={() => setAll(true)}>
          Show all {lines.length.toLocaleString()} lines
        </button>
      )}
    </>
  );
}

/** Code with a line-number gutter; highlighted as one block so multi-line tokens stay correct. */
function NumberedCode({ lines, numbers, lang }: { lines: string[]; numbers?: string[]; lang?: string }) {
  const [all, setAll] = useState(false);
  const n = all ? lines.length : Math.min(lines.length, RENDER_CAP);
  const code = lines.slice(0, n).join("\n");
  const gutter = (numbers ?? lines.map((_, i) => String(i + 1))).slice(0, n);
  return (
    <div className="tx-code" data-testid="numbered-code">
      <div className="flex min-w-max">
        <pre className="tx-gutter" aria-hidden="true">
          {gutter.join("\n")}
        </pre>
        <pre className="tx-codebody hljs">
          <Highlighted code={code} lang={lang} />
        </pre>
      </div>
      {lines.length > n && (
        <button type="button" className="tx-more px-2 pb-1" onClick={() => setAll(true)}>
          Show all {lines.length.toLocaleString()} lines
        </button>
      )}
    </div>
  );
}

function DiffBlock({ lines }: { lines: (DiffLine | null)[] }) {
  return (
    <div className="tx-code" data-testid="edit-diff">
      <div className="min-w-max">
        {lines.map((l, i) =>
          l === null ? (
            <div key={i} className="tx-diff-gap">
              ⋮
            </div>
          ) : (
            <div key={i} data-kind={l.kind} className={`tx-diff-line tx-diff-${l.kind}`}>
              <span className="tx-diff-n">{l.kind === "add" ? l.newN : l.oldN}</span>
              <span className="tx-diff-sign">{l.kind === "add" ? "+" : l.kind === "del" ? "-" : " "}</span>
              <span className="whitespace-pre">{l.text || " "}</span>
            </div>
          ),
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- per-tool bodies

interface BodyProps {
  args: Record<string, unknown>;
  output: string;
  isError: boolean;
  open: boolean;
  setOpen: (v: boolean) => void;
}

function BashBody({ args, output, isError, open, setOpen }: BodyProps) {
  const b = useMemo(() => parseBash(output), [output]);
  const out = splitLines(b.stdout);
  const err = splitLines(b.stderr);
  const all = [...out, ...err];
  const failed = isError || (b.exitCode !== null && b.exitCode !== 0);
  const cmd = bashCommand(args);
  const multiline = cmd.trim().includes("\n") || cmd.length > 80;
  return (
    <Result tone={failed ? "error" : "dim"}>
      {open && multiline && <pre className="tx-pre mb-1 text-neutral-500 dark:text-neutral-400">$ {cmd}</pre>}
      {all.length === 0 && <div>(No content)</div>}
      {open ? (
        <>
          {out.length > 0 && <Lines lines={out} className={failed ? "" : "text-neutral-700 dark:text-neutral-300"} />}
          {err.length > 0 && <Lines lines={err} className="text-red-700 dark:text-red-400" />}
          {all.length > PREVIEW_LINES && <MoreLines n={0} expanded onClick={() => setOpen(false)} />}
        </>
      ) : (
        <>
          {all.length > 0 && (
            <pre className={`tx-pre ${failed ? "" : "text-neutral-700 dark:text-neutral-300"}`}>
              {all.slice(0, PREVIEW_LINES).join("\n")}
            </pre>
          )}
          <MoreLines n={all.length - PREVIEW_LINES} onClick={() => setOpen(true)} />
        </>
      )}
      {b.timedOut && <div className="text-amber-700 dark:text-amber-400">Timed out and was killed</div>}
    </Result>
  );
}

function ReadBody({ args, output, isError, open, setOpen }: BodyProps) {
  const rows = useMemo(() => parseNumbered(output), [output]);
  if (isError) return <ErrorResult output={output} />;
  return (
    <Result>
      <button type="button" className="tx-link" onClick={() => setOpen(!open)}>
        Read <span className="font-semibold text-neutral-700 dark:text-neutral-300">{rows.length}</span>{" "}
        {rows.length === 1 ? "line" : "lines"}
        {rows.length > 0 && <span className="tx-hint">{open ? " (click to collapse)" : " (click to expand)"}</span>}
      </button>
      {open && rows.length > 0 && (
        <NumberedCode lines={rows.map((r) => r.text)} numbers={rows.map((r) => r.n)} lang={langFromPath(str(args.path))} />
      )}
    </Result>
  );
}

function WriteBody({ args, output, isError, open, setOpen }: BodyProps) {
  const content = str(args.content);
  const lines = useMemo(() => splitLines(content), [content]);
  if (isError) return <ErrorResult output={output} />;
  return (
    <Result>
      <button type="button" className="tx-link" onClick={() => setOpen(!open)}>
        Wrote <span className="font-semibold text-neutral-700 dark:text-neutral-300">{lines.length}</span>{" "}
        {lines.length === 1 ? "line" : "lines"} to{" "}
        <span className="font-semibold text-neutral-700 dark:text-neutral-300">{str(args.path)}</span>
        {lines.length > 0 && <span className="tx-hint">{open ? " (click to collapse)" : " (click to expand)"}</span>}
      </button>
      {open && lines.length > 0 && <NumberedCode lines={lines} lang={langFromPath(str(args.path))} />}
    </Result>
  );
}

function UpdateBody({ args, output, isError, open, setOpen }: BodyProps) {
  const diff = useMemo(() => lineDiff(str(args.old), str(args.new)), [args.old, args.new]);
  const shown = useMemo(() => withContext(diff, 3), [diff]);
  if (isError) return <ErrorResult output={output} />;
  const { added, removed } = diffStats(diff);
  const visible = open ? shown : shown.slice(0, DIFF_PREVIEW);
  return (
    <Result>
      <div data-testid="diff-stats">
        Added <span className="font-semibold text-neutral-700 dark:text-neutral-300">{added}</span>{" "}
        {added === 1 ? "line" : "lines"}, removed{" "}
        <span className="font-semibold text-neutral-700 dark:text-neutral-300">{removed}</span>{" "}
        {removed === 1 ? "line" : "lines"}
      </div>
      {shown.length > 0 && <DiffBlock lines={visible} />}
      {shown.length > DIFF_PREVIEW && (
        <MoreLines n={shown.length - DIFF_PREVIEW} expanded={open} onClick={() => setOpen(!open)} />
      )}
    </Result>
  );
}

function ErrorResult({ output }: { output: string }) {
  const lines = splitLines(output);
  return (
    <Result tone="error">
      <Lines lines={lines.length ? lines : ["(error)"]} />
    </Result>
  );
}

function GenericBody({ args, output, isError, open, setOpen }: BodyProps) {
  const lines = splitLines(output);
  return (
    <Result tone={isError ? "error" : "plain"}>
      {open && <pre className="tx-pre mb-1 text-neutral-500 dark:text-neutral-400">{JSON.stringify(args, null, 2)}</pre>}
      {lines.length === 0 && <div className="text-neutral-500">(No content)</div>}
      {open ? <Lines lines={lines} /> : <pre className="tx-pre">{lines.slice(0, PREVIEW_LINES).join("\n")}</pre>}
      <MoreLines
        n={lines.length - PREVIEW_LINES}
        expanded={open && lines.length > PREVIEW_LINES}
        onClick={() => setOpen(!open)}
      />
    </Result>
  );
}

const BODIES: Record<string, (p: BodyProps) => JSX.Element> = {
  bash: BashBody,
  read_file: ReadBody,
  write_file: WriteBody,
  edit_file: UpdateBody,
};

export function ToolBlock({ call, result, now }: { call: ForgeEvent; result?: ForgeEvent; now: number }) {
  const [open, setOpen] = useExpanded();
  const name = str(call.payload.name);
  const args = (call.payload.args && typeof call.payload.args === "object" ? call.payload.args : {}) as Record<
    string,
    unknown
  >;
  const output = str(result?.payload.output);
  let isError = Boolean(result?.payload.is_error);
  let exitCode: number | null = null;
  if (name === "bash" && result) {
    exitCode = parseBash(output).exitCode;
    if (exitCode !== null && exitCode !== 0) isError = true;
  }
  const { tool, arg } = toolLabel(name, args);
  const Body = BODIES[name] ?? GenericBody;
  const pending = !result;
  const tone = pending ? "pending" : isError ? "error" : "ok";
  return (
    <div
      className="tx-row"
      data-testid="tool-card"
      data-tool={name}
      data-status={pending ? "pending" : isError ? "error" : "ok"}
      data-error={isError ? "true" : undefined}
    >
      <Dot tone={tone} />
      <div className="min-w-0 flex-1">
        <button
          type="button"
          className="tx-head"
          data-testid="tool-header"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          <span className="font-semibold text-neutral-900 dark:text-neutral-100">{tool}</span>
          <span className="text-neutral-600 dark:text-neutral-400">
            (<span className="break-all">{arg}</span>)
          </span>
          {exitCode !== null && exitCode !== 0 && (
            <span className="tx-badge" data-testid="exit-code">
              exit {exitCode}
            </span>
          )}
          {pending && (
            <span className="ml-2 inline-flex items-center gap-1.5 text-neutral-500">
              <Spinner /> <span data-testid="tool-elapsed">{fmtElapsed(Math.max(0, now - call.ts))}</span>
            </span>
          )}
        </button>
        {pending ? (
          <Result>
            <span>Running…</span>
          </Result>
        ) : (
          <Body args={args} output={output} isError={isError} open={open} setOpen={setOpen} />
        )}
      </div>
    </div>
  );
}
