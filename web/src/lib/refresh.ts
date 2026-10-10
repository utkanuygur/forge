import { useEffect, useRef } from "react";

/**
 * App-wide "this data changed" signal. The assistant panel publishes the `refresh` keys of each successful tool
 * result (for example `evals:repo-maintainer`); pages subscribe to the keys they display and refetch.
 */
const EVENT = "forge:refresh";

export function publishRefresh(keys: string[]): void {
  if (keys.length) window.dispatchEvent(new CustomEvent<string[]>(EVENT, { detail: keys }));
}

/** Calls `onRefresh` when any published key is in `keys`. */
export function useRefresh(keys: string[], onRefresh: () => void): void {
  const cb = useRef(onRefresh);
  cb.current = onRefresh;
  const joined = keys.join("|");
  useEffect(() => {
    const want = new Set(joined.split("|").filter(Boolean));
    const handler = (e: Event) => {
      const got = (e as CustomEvent<string[]>).detail || [];
      if (got.some((k) => want.has(k))) cb.current();
    };
    window.addEventListener(EVENT, handler);
    return () => window.removeEventListener(EVENT, handler);
  }, [joined]);
}
