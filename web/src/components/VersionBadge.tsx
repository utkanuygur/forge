import { useEffect, useId, useRef, useState, type FocusEvent } from "react";
import { api, type BuildInfo } from "../api";

export const RELEASES_URL = "https://github.com/utkanuygur/forge/releases/tag/";

// "v0.1.3", "v0.1.4-preview" or "dev". Null when the server knows nothing about its build.
export function versionLabel(info: BuildInfo): string | null {
  if (info.channel === "dev") return "dev";
  if (info.version) return `v${info.version}`;
  return null;
}

export function fmtBuiltAt(builtAt: string | null): string {
  if (!builtAt) return "unknown";
  const d = new Date(builtAt);
  if (Number.isNaN(d.getTime())) return builtAt;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function VersionBadgeView({ info }: { info: BuildInfo }) {
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const tipId = useId();
  const label = versionLabel(info);
  if (!label && !info.commit) return null;

  const preview = info.channel === "preview";
  const channel = info.channel ?? "unknown";
  const aria = [`Forge version ${info.version ?? (info.channel === "dev" ? "dev build" : "unknown")}`, channel];
  if (info.commit) aria.push(`commit ${info.commit}`);
  const leaving = (e: FocusEvent) => {
    if (!wrapper.current?.contains(e.relatedTarget as Node | null)) setOpen(false);
  };

  return (
    <div
      ref={wrapper}
      className="relative -ml-4 flex items-center"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={leaving}
      onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
    >
      <button
        type="button"
        data-testid="version-badge"
        data-channel={info.channel ?? ""}
        aria-label={aria.join(", ")}
        aria-describedby={open ? tipId : undefined}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[11px] leading-4 text-neutral-500 hover:text-neutral-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 dark:text-neutral-400 dark:hover:text-neutral-200 ${
          preview ? "ring-1 ring-accent-300/70 dark:ring-accent-700/70" : ""
        }`}
      >
        {label && <span data-testid="version-badge-version">{label}</span>}
        {info.commit && (
          <span data-testid="version-badge-commit" className="max-[479px]:hidden">
            {label ? " · " : ""}
            {info.commit}
          </span>
        )}
      </button>
      {open && (
        <div
          id={tipId}
          role="tooltip"
          data-testid="version-tooltip"
          className="absolute left-0 top-full z-30 mt-2 w-64 max-w-[calc(100vw-2rem)] rounded-lg border border-neutral-200 bg-white p-3 text-xs shadow-lg dark:border-neutral-800 dark:bg-neutral-900"
        >
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-neutral-500 dark:text-neutral-400">Version</dt>
            <dd className="font-mono text-neutral-900 dark:text-neutral-100">{info.version ?? "unknown"}</dd>
            <dt className="text-neutral-500 dark:text-neutral-400">Channel</dt>
            <dd className="text-neutral-900 dark:text-neutral-100">{channel}</dd>
            <dt className="text-neutral-500 dark:text-neutral-400">Commit</dt>
            <dd className="font-mono text-neutral-900 dark:text-neutral-100">{info.commit ?? "unknown"}</dd>
            <dt className="text-neutral-500 dark:text-neutral-400">Built</dt>
            <dd className="text-neutral-900 dark:text-neutral-100">{fmtBuiltAt(info.built_at)}</dd>
          </dl>
          {info.channel === "release" && info.version && (
            <a
              href={`${RELEASES_URL}v${info.version}`}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block font-medium text-accent-600 hover:underline dark:text-accent-400"
            >
              What's new
            </a>
          )}
        </div>
      )}
    </div>
  );
}

// Header badge: loads GET /v1/version once and renders nothing until it answers (or if it fails).
export default function VersionBadge() {
  const [info, setInfo] = useState<BuildInfo | null>(null);
  useEffect(() => {
    let live = true;
    api
      .version()
      .then((v) => live && v && typeof v === "object" && setInfo(v))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  return info ? <VersionBadgeView info={info} /> : null;
}
