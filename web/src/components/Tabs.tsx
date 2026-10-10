export interface TabDef {
  key: string;
  label: string;
}

export default function Tabs({ tabs, active, onChange }: { tabs: TabDef[]; active: string; onChange: (k: string) => void }) {
  return (
    <div className="-mx-4 overflow-x-auto border-b border-neutral-200 px-4 dark:border-neutral-800 sm:mx-0 sm:px-0">
      <div className="flex gap-1" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={active === t.key}
            onClick={() => onChange(t.key)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition ${
              active === t.key
                ? "border-accent-600 text-accent-700 dark:border-accent-400 dark:text-accent-300"
                : "border-transparent text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
    </div>
  );
}
