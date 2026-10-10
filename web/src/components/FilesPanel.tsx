import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, File, FileText, Folder, RefreshCw, X } from "lucide-react";
import { api, ApiError, errMsg, type WorkspaceFile } from "../api";
import { highlightLines, langFromPath, type TokenKind } from "./transcript/highlight";

// Browses a run's exported workspace: a collapsible tree (changed files highlighted) and a viewer with line
// numbers and syntax highlighting (lowlight, the same grammars as the transcript). Standalone so the Playground can mount it in a slot.

export interface TreeDir {
  name: string;
  path: string;
  dirs: TreeDir[];
  files: WorkspaceFile[];
  changed: number; // changed files anywhere below
}

export function buildTree(files: WorkspaceFile[]): TreeDir {
  const root: TreeDir = { name: "", path: "", dirs: [], files: [], changed: 0 };
  for (const f of files) {
    const parts = f.path.split("/");
    let dir = root;
    if (f.changed) root.changed++;
    for (let i = 0; i < parts.length - 1; i++) {
      const path = parts.slice(0, i + 1).join("/");
      let next = dir.dirs.find((d) => d.name === parts[i]);
      if (!next) {
        next = { name: parts[i], path, dirs: [], files: [], changed: 0 };
        dir.dirs.push(next);
      }
      if (f.changed) next.changed++;
      dir = next;
    }
    dir.files.push(f);
  }
  const sort = (d: TreeDir) => {
    d.dirs.sort((a, b) => a.name.localeCompare(b.name));
    d.files.sort((a, b) => a.path.localeCompare(b.path));
    d.dirs.forEach(sort);
  };
  sort(root);
  return root;
}

/** Directories open by default: all of them for small workspaces, else the ones holding changed files. */
function defaultOpen(root: TreeDir, total: number): Set<string> {
  const open = new Set<string>();
  const walk = (d: TreeDir) => {
    for (const c of d.dirs) {
      if (total <= 40 || c.changed > 0) open.add(c.path);
      walk(c);
    }
  };
  walk(root);
  return open;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const TOKEN_CLS: Record<TokenKind, string> = {
  comment: "text-neutral-500 italic dark:text-neutral-500",
  string: "text-emerald-700 dark:text-emerald-400",
  number: "text-amber-700 dark:text-amber-400",
  keyword: "font-semibold text-accent-700 dark:text-accent-300",
};

const CHANGE_LABEL: Record<string, string> = { added: "A", modified: "M", renamed: "R" };

export function CodeView({ path, text }: { path: string; text: string }) {
  const lines = useMemo(() => highlightLines(text, langFromPath(path)), [path, text]);
  const width = String(lines.length).length;
  return (
    <div
      data-testid="file-content"
      data-path={path}
      className="overflow-auto rounded-lg border border-neutral-200 bg-white font-mono text-xs leading-5 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <table className="min-w-max border-collapse">
        <tbody>
          {lines.map((toks, i) => (
            <tr key={i} data-line={i + 1}>
              <td
                className="select-none border-r border-neutral-200 px-3 text-right align-top text-neutral-400 dark:border-neutral-800"
                style={{ minWidth: `${width + 2}ch` }}
              >
                {i + 1}
              </td>
              <td className="whitespace-pre px-3 text-neutral-800 dark:text-neutral-200">
                {toks.length === 0
                  ? " "
                  : toks.map((t, j) =>
                      t.kind ? (
                        <span key={j} data-token={t.kind} className={TOKEN_CLS[t.kind]}>
                          {t.text}
                        </span>
                      ) : (
                        <span key={j}>{t.text}</span>
                      ),
                    )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TreeNode({
  dir,
  depth,
  open,
  toggle,
  selected,
  select,
}: {
  dir: TreeDir;
  depth: number;
  open: Set<string>;
  toggle: (path: string) => void;
  selected: string | null;
  select: (f: WorkspaceFile) => void;
}) {
  const pad = { paddingLeft: `${depth * 14 + 8}px` };
  return (
    <>
      {dir.dirs.map((d) => {
        const isOpen = open.has(d.path);
        return (
          <li key={`d:${d.path}`} role="treeitem" aria-expanded={isOpen} aria-label={d.name}>
            <button
              type="button"
              onClick={() => toggle(d.path)}
              data-testid="tree-dir"
              data-path={d.path}
              className="flex w-full items-center gap-1.5 rounded py-1 pr-2 text-left hover:bg-neutral-100 dark:hover:bg-neutral-800"
              style={pad}
            >
              {isOpen ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
              <Folder className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
              <span className="truncate">{d.name}</span>
              {d.changed > 0 && <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" title="Contains changed files" />}
            </button>
            {isOpen && (
              <ul role="group">
                <TreeNode dir={d} depth={depth + 1} open={open} toggle={toggle} selected={selected} select={select} />
              </ul>
            )}
          </li>
        );
      })}
      {dir.files.map((f) => {
        const name = f.path.split("/").pop();
        const active = selected === f.path;
        return (
          <li key={`f:${f.path}`} role="treeitem" aria-selected={active} aria-label={name}>
            <button
              type="button"
              onClick={() => select(f)}
              data-testid="tree-file"
              data-path={f.path}
              data-changed={f.changed ? "true" : "false"}
              title={`${f.path} (${fmtBytes(f.size)})`}
              className={`flex w-full items-center gap-1.5 rounded py-1 pr-2 text-left ${
                active ? "bg-accent-50 text-accent-800 dark:bg-accent-900/40 dark:text-accent-200" : "hover:bg-neutral-100 dark:hover:bg-neutral-800"
              } ${f.changed ? "font-medium text-amber-800 dark:text-amber-300" : ""}`}
              style={{ paddingLeft: `${depth * 14 + 26}px` }}
            >
              {f.is_text ? <FileText className="h-3.5 w-3.5 shrink-0 text-neutral-400" /> : <File className="h-3.5 w-3.5 shrink-0 text-neutral-400" />}
              <span className="truncate">{name}</span>
              {f.changed && f.change && (
                <span
                  className="ml-auto shrink-0 rounded bg-amber-100 px-1 font-mono text-[10px] text-amber-800 dark:bg-amber-900/50 dark:text-amber-200"
                  title={f.change}
                >
                  {CHANGE_LABEL[f.change] ?? "M"}
                </span>
              )}
            </button>
          </li>
        );
      })}
    </>
  );
}

type Content = { state: "loading" } | { state: "ok"; text: string } | { state: "error"; message: string };

export default function FilesPanel({ runId, share, onClose }: { runId: string; share?: string; onClose?: () => void }) {
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<WorkspaceFile | null>(null);
  const [content, setContent] = useState<Content | null>(null);
  const [changedOnly, setChangedOnly] = useState(false);
  const [reload, setReload] = useState(0);
  const wanted = useRef<string | null>(null); // ignore responses for a file that is no longer selected

  const openFile = useCallback(
    (f: WorkspaceFile) => {
      setSelected(f);
      const key = `${runId}:${f.path}`;
      wanted.current = key;
      if (!f.is_text) {
        setContent(null);
        return;
      }
      setContent({ state: "loading" });
      api
        .readFile(runId, f.path, share)
        .then((text) => {
          if (wanted.current === key) setContent({ state: "ok", text: typeof text === "string" ? text : JSON.stringify(text, null, 2) });
        })
        .catch((e) =>
          wanted.current === key &&
          setContent({
            state: "error",
            message: e instanceof ApiError && e.status === 413 ? "This file is larger than 1 MB and is not shown." : errMsg(e),
          }),
        );
    },
    [runId, share],
  );

  useEffect(() => {
    let cancelled = false;
    setFiles(null);
    setError(null);
    setSelected(null);
    setContent(null);
    wanted.current = null;
    api
      .listFiles(runId, true, share)
      .then((list) => {
        if (cancelled) return;
        setFiles(list);
        setOpen(defaultOpen(buildTree(list), list.length));
        const first = list.find((f) => f.changed && f.is_text);
        if (first) openFile(first);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof ApiError && e.status === 404 ? "This run has no saved workspace yet." : errMsg(e));
      });
    return () => {
      cancelled = true;
    };
  }, [runId, share, reload, openFile]);

  const visible = useMemo(() => (files && changedOnly ? files.filter((f) => f.changed) : files) ?? [], [files, changedOnly]);
  const tree = useMemo(() => buildTree(visible), [visible]);
  const changedCount = files?.filter((f) => f.changed).length ?? 0;

  const toggle = (path: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  return (
    <div data-testid="files-panel" className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-neutral-600 dark:text-neutral-400">
          {files ? `${files.length} ${files.length === 1 ? "file" : "files"}, ${changedCount} changed` : "Workspace files"}
        </span>
        {files && changedCount > 0 && (
          <label className="flex items-center gap-1.5 text-xs text-neutral-600 dark:text-neutral-400">
            <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} />
            Changed only
          </label>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button type="button" className="btn-ghost btn-sm" onClick={() => setReload((n) => n + 1)} aria-label="Refresh files">
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          {onClose && (
            <button type="button" className="btn-ghost btn-sm" onClick={onClose} aria-label="Close files">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {error ? (
        <p role="alert" data-testid="files-error" className="text-sm text-neutral-500">
          {error}
        </p>
      ) : files === null ? (
        <p className="text-sm text-neutral-500">Loading files</p>
      ) : files.length === 0 ? (
        <p data-testid="files-empty" className="text-sm text-neutral-500">
          The workspace is empty.
        </p>
      ) : (
        <div className="grid gap-3 md:grid-cols-[minmax(12rem,16rem)_1fr]">
          <nav aria-label="Workspace files" className="max-h-[28rem] overflow-auto rounded-lg border border-neutral-200 py-1 text-xs dark:border-neutral-800">
            <ul role="tree">
              <TreeNode dir={tree} depth={0} open={open} toggle={toggle} selected={selected?.path ?? null} select={openFile} />
            </ul>
          </nav>
          <div className="min-w-0">
            {!selected ? (
              <p className="text-sm text-neutral-500">Select a file to view it.</p>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs text-neutral-500">
                  <span className="truncate font-mono text-neutral-800 dark:text-neutral-200">{selected.path}</span>
                  <span>{fmtBytes(selected.size)}</span>
                  {selected.changed && <span className="text-amber-700 dark:text-amber-300">{selected.change ?? "changed"}</span>}
                </div>
                {!selected.is_text ? (
                  <p data-testid="file-binary" className="text-sm text-neutral-500">
                    Binary file, not shown. Download the workspace archive to open it.
                  </p>
                ) : content?.state === "loading" ? (
                  <p className="text-sm text-neutral-500">Loading file</p>
                ) : content?.state === "error" ? (
                  <p role="alert" className="text-sm text-red-600 dark:text-red-400">
                    {content.message}
                  </p>
                ) : content?.state === "ok" ? (
                  <CodeView path={selected.path} text={content.text} />
                ) : null}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
