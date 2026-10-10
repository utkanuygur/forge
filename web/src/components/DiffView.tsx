export default function DiffView({ diff }: { diff: string }) {
  const lines = diff.replace(/\n$/, "").split("\n");
  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white font-mono text-xs leading-5 dark:border-neutral-800 dark:bg-neutral-950">
      <pre className="min-w-max py-2">
        {lines.map((line, i) => {
          let cls = "text-neutral-700 dark:text-neutral-300";
          if (line.startsWith("+++") || line.startsWith("---")) cls = "font-semibold text-neutral-900 dark:text-neutral-100";
          else if (line.startsWith("diff ") || line.startsWith("index ")) cls = "text-neutral-500 dark:text-neutral-500";
          else if (line.startsWith("@@")) cls = "bg-accent-50 text-accent-700 dark:bg-accent-900/30 dark:text-accent-300";
          else if (line.startsWith("+")) cls = "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300";
          else if (line.startsWith("-")) cls = "bg-red-50 text-red-800 dark:bg-red-950/60 dark:text-red-300";
          return (
            <div key={i} className={`px-3 ${cls}`}>
              {line || " "}
            </div>
          );
        })}
      </pre>
    </div>
  );
}
