import { useCallback, useEffect, useState } from "react";

export const MIN_WIDTH = 360;
export const MAX_WIDTH = 520;
export const DEFAULT_WIDTH = 400;
const OPEN_KEY = "forge.assistant.open";
const WIDTH_KEY = "forge.assistant.width";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the panel still works, it just does not remember */
  }
}

export function clampWidth(w: number): number {
  if (!Number.isFinite(w)) return DEFAULT_WIDTH;
  return Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, w)));
}

/** Open state and width of the assistant panel, remembered per browser; Cmd+J / Ctrl+J toggles it. */
export function usePanel() {
  const [open, setOpen] = useState(() => read(OPEN_KEY) === "1");
  const [width, setWidthState] = useState(() => clampWidth(Number(read(WIDTH_KEY) || DEFAULT_WIDTH)));

  useEffect(() => write(OPEN_KEY, open ? "1" : "0"), [open]);

  const setWidth = useCallback((w: number) => {
    const c = clampWidth(w);
    setWidthState(c);
    write(WIDTH_KEY, String(c));
  }, []);

  const toggle = useCallback(() => setOpen((o) => !o), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return { open, setOpen, toggle, width, setWidth };
}

/** True at tablet width and up, where the panel sits beside the page instead of covering it. */
export function useWide(query = "(min-width: 768px)"): boolean {
  const get = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : true);
  const [wide, setWide] = useState(get);
  useEffect(() => {
    if (!window.matchMedia) return;
    const m = window.matchMedia(query);
    const on = () => setWide(m.matches);
    on();
    m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, [query]);
  return wide;
}
