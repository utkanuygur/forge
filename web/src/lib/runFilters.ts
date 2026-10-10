// Run search filters <-> query string for GET /v1/agents/{slug}/runs and GET /v1/runs.

export interface RunFilters {
  status?: string;
  version?: string;
  source?: string;
  rating?: "" | "up" | "down" | "none";
  toolErrors?: boolean;
  q?: string;
  sort?: "created_at" | "duration" | "cost";
  order?: "asc" | "desc";
  since?: number;
  until?: number;
  agent?: string;
  limit?: number;
  offset?: number;
}

/** Builds the query string (without "?"), omitting unset values; keys are sorted for stable URLs. */
export function buildRunQuery(f: RunFilters): string {
  const p: [string, string][] = [];
  const add = (k: string, v: string | number | undefined | null) => {
    if (v === undefined || v === null || v === "") return;
    p.push([k, String(v)]);
  };
  add("agent", f.agent);
  add("status", f.status);
  add("version", f.version);
  add("source", f.source);
  add("rating", f.rating);
  if (f.toolErrors) add("has_tool_errors", "true");
  add("q", f.q?.trim());
  if (f.sort && f.sort !== "created_at") add("sort", f.sort);
  if (f.order && f.order !== "desc") add("order", f.order);
  add("since", f.since);
  add("until", f.until);
  add("limit", f.limit);
  if (f.offset) add("offset", f.offset);
  p.sort((a, b) => a[0].localeCompare(b[0]));
  return p.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
}

/** True when any narrowing filter (not sort or paging) is set. */
export function hasActiveFilters(f: RunFilters): boolean {
  return Boolean(f.status || f.version || f.source || f.rating || f.toolErrors || f.q?.trim() || f.since || f.until);
}
