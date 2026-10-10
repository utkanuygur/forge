import { createLowlight } from "lowlight";
import { toJsxRuntime } from "hast-util-to-jsx-runtime";
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import makefile from "highlight.js/lib/languages/makefile";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

/** A small, fixed grammar set keeps the bundle lean; anything else renders as plain text. */
export const LANGUAGES = {
  bash, css, diff, go, ini, javascript, json, makefile, markdown, python, rust, sql, typescript, xml, yaml,
};
export const ALIASES: Record<string, string[]> = {
  bash: ["sh", "shell", "zsh", "console"],
  javascript: ["js", "jsx", "mjs", "cjs"],
  typescript: ["ts", "tsx"],
  python: ["py"],
  xml: ["html", "svg"],
  ini: ["toml", "cfg"],
  yaml: ["yml"],
  markdown: ["md"],
  rust: ["rs"],
};

const lowlight = createLowlight(LANGUAGES);
lowlight.registerAlias(ALIASES);

const EXT: Record<string, string> = {
  py: "python", js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript", ts: "typescript",
  tsx: "typescript", json: "json", sh: "bash", bash: "bash", zsh: "bash", css: "css", html: "xml", xml: "xml",
  svg: "xml", yml: "yaml", yaml: "yaml", toml: "ini", ini: "ini", cfg: "ini", md: "markdown", go: "go", rs: "rust",
  sql: "sql", diff: "diff", patch: "diff",
};

export function langFromPath(path: string): string | undefined {
  const base = path.split("/").pop() || "";
  if (/^makefile$/i.test(base)) return "makefile";
  const ext = base.includes(".") ? base.split(".").pop()!.toLowerCase() : "";
  return EXT[ext];
}

/** Syntax-highlighted code as React elements (hast tree, never raw HTML). */
export function Highlighted({ code, lang }: { code: string; lang?: string }) {
  if (!lang || !lowlight.registered(lang)) return <>{code}</>;
  try {
    const tree = lowlight.highlight(lang, code);
    return toJsxRuntime(tree, { Fragment, jsx, jsxs });
  } catch {
    return <>{code}</>;
  }
}

// ---------------------------------------------------------------- line tokens (FilesPanel code view)

export type TokenKind = "comment" | "string" | "number" | "keyword";
export interface Token {
  kind: TokenKind | null;
  text: string;
}

const KIND_OF: Record<string, TokenKind> = {
  "hljs-comment": "comment",
  "hljs-quote": "comment",
  "hljs-string": "string",
  "hljs-regexp": "string",
  "hljs-number": "number",
  "hljs-keyword": "keyword",
  "hljs-built_in": "keyword",
  "hljs-literal": "keyword",
  "hljs-selector-tag": "keyword",
};

interface HastNode {
  type: string;
  value?: string;
  properties?: { className?: string[] | string };
  children?: HastNode[];
}

/** The same lowlight grammars as the transcript, flattened into per-line tokens so a code view can render
 * one table row per line (a string or comment that spans lines is split at the line breaks). */
export function highlightLines(code: string, lang?: string | null): Token[][] {
  const flat: Token[] = [];
  const walk = (node: HastNode, kind: TokenKind | null) => {
    if (node.type === "text") {
      flat.push({ kind, text: node.value ?? "" });
      return;
    }
    const cls = node.properties?.className;
    const classes = Array.isArray(cls) ? cls : cls ? [cls] : [];
    const own = classes.map((c) => KIND_OF[c]).find(Boolean) ?? kind;
    for (const child of node.children ?? []) walk(child, own);
  };
  if (lang && lowlight.registered(lang)) {
    try {
      walk(lowlight.highlight(lang, code) as unknown as HastNode, null);
    } catch {
      flat.splice(0, flat.length, { kind: null, text: code });
    }
  } else {
    flat.push({ kind: null, text: code });
  }
  const lines: Token[][] = [[]];
  for (const t of flat) {
    t.text.split("\n").forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ kind: t.kind, text: part });
    });
  }
  if (code.endsWith("\n") && lines.length > 1 && lines[lines.length - 1].length === 0) lines.pop();
  return lines;
}
