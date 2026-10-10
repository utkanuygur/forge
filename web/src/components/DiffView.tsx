export type DiffLineKind = "file" | "meta" | "hunk" | "add" | "del" | "context";

/**
 * Classify unified diff lines. Inside a hunk, `+`/`-` lines are content even when they look like
 * `+++`/`---` file headers (e.g. a removed SQL comment `-- x` shows as `--- x`).
 */
export function classifyDiffLines(lines: string[]): DiffLineKind[] {
  let inHunk = false;
  return lines.map((line) => {
    if (line.startsWith("diff ")) {
      inHunk = false;
      return "meta";
    }
    if (line.startsWith("@@")) {
      inHunk = true;
      return "hunk";
    }
    if (!inHunk) {
      if (line.startsWith("+++") || line.startsWith("---")) return "file";
      if (line.startsWith("index ") || line.startsWith("new file") || line.startsWith("deleted file") || line.startsWith("similarity") || line.startsWith("rename ") || line.startsWith("old mode") || line.startsWith("new mode") || line.startsWith("Binary files")) return "meta";
    }
    if (line.startsWith("+")) return "add";
    if (line.startsWith("-")) return "del";
    return "context";
  });
}

const CLS: Record<DiffLineKind, string> = {
  file: "font-semibold text-neutral-900 dark:text-neutral-100",
  meta: "text-neutral-500 dark:text-neutral-500",
  hunk: "bg-accent-50 text-accent-700 dark:bg-accent-900/30 dark:text-accent-300",
  add: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300",
  del: "bg-red-50 text-red-800 dark:bg-red-950/60 dark:text-red-300",
  context: "text-neutral-700 dark:text-neutral-300",
};

export default function DiffView({ diff }: { diff: string }) {
  const lines = diff.replace(/\n$/, "").split("\n");
  const kinds = classifyDiffLines(lines);
  return (
    <div
      data-testid="diff-view"
      className="overflow-x-auto rounded-lg border border-neutral-200 bg-white font-mono text-xs leading-5 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <pre className="min-w-max py-2">
        {lines.map((line, i) => (
          <div key={i} data-kind={kinds[i]} className={`px-3 ${CLS[kinds[i]]}`}>
            {line || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}
