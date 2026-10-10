// A small, dependency-free syntax highlighter for the workspace file viewer.
// It recognizes comments, strings, numbers and keywords for common languages; everything else is plain text.
// Output is data (token lists), never HTML, so rendering stays safe for arbitrary file contents.

export type TokenKind = "comment" | "string" | "number" | "keyword";
export interface Token {
  kind: TokenKind | null;
  text: string;
}

interface Lang {
  comment: string[]; // regex sources
  string: string[];
  keywords: string[];
}

const DQ = String.raw`"(?:\\.|[^"\\\n])*"`;
const SQ = String.raw`'(?:\\.|[^'\\\n])*'`;
const BT = String.raw`\x60(?:\\.|[^\x60\\])*\x60`;
const HASH = String.raw`#.*`;
const SLASH = String.raw`//.*`;
const BLOCK = String.raw`/\*[\s\S]*?\*/`;

const JS_KW =
  "async await break case catch class const continue debugger default delete do else export extends false finally for from function if import in instanceof interface let new null of return static super switch this throw true try type typeof undefined var void while yield enum implements private protected public readonly as";

const LANGS: Record<string, Lang> = {
  python: {
    comment: [HASH],
    string: [String.raw`[rbfu]{0,2}"""[\s\S]*?"""`, String.raw`[rbfu]{0,2}'''[\s\S]*?'''`, `[rbfu]{0,2}${DQ}`, `[rbfu]{0,2}${SQ}`],
    keywords: "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield match case self print".split(" "),
  },
  js: { comment: [SLASH, BLOCK], string: [DQ, SQ, BT], keywords: JS_KW.split(" ") },
  shell: {
    comment: [HASH],
    string: [DQ, SQ],
    keywords: "if then else elif fi for while do done case esac in function return export local set echo cd exit".split(" "),
  },
  json: { comment: [], string: [DQ], keywords: ["true", "false", "null"] },
  yaml: { comment: [HASH], string: [DQ, SQ], keywords: ["true", "false", "null", "yes", "no", "on", "off"] },
  go: {
    comment: [SLASH, BLOCK],
    string: [DQ, BT, SQ],
    keywords: "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false".split(" "),
  },
  rust: {
    comment: [SLASH, BLOCK],
    string: [DQ],
    keywords: "as async await break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while".split(" "),
  },
  c: {
    comment: [SLASH, BLOCK],
    string: [DQ, SQ],
    keywords: "auto break case char const continue default do double else enum extern float for goto if int long register return short signed sizeof static struct switch typedef union unsigned void volatile while class public private protected new delete true false null this namespace using template bool".split(" "),
  },
  css: { comment: [BLOCK], string: [DQ, SQ], keywords: ["important", "media", "import"] },
  sql: {
    comment: [String.raw`--.*`, BLOCK],
    string: [SQ, DQ],
    keywords: "select from where insert into values update set delete create table drop alter and or not null primary key join left right inner outer on group by order having limit as distinct index".split(" "),
  },
  html: { comment: [String.raw`<!--[\s\S]*?-->`], string: [DQ, SQ], keywords: [] },
  toml: { comment: [HASH], string: [String.raw`"""[\s\S]*?"""`, DQ, SQ], keywords: ["true", "false"] },
};

const EXT: Record<string, string> = {
  py: "python", pyi: "python",
  js: "js", jsx: "js", mjs: "js", cjs: "js", ts: "js", tsx: "js",
  sh: "shell", bash: "shell", zsh: "shell",
  json: "json",
  yml: "yaml", yaml: "yaml",
  go: "go",
  rs: "rust",
  c: "c", h: "c", cc: "c", cpp: "c", hpp: "c", java: "c", cs: "c", kt: "c", swift: "c",
  css: "css", scss: "css",
  sql: "sql",
  html: "html", htm: "html", xml: "html", svg: "html", vue: "html",
  toml: "toml", ini: "toml", cfg: "toml",
};

const NAMES: Record<string, string> = { Makefile: "shell", Dockerfile: "shell", ".bashrc": "shell", ".zshrc": "shell" };

export function languageFor(path: string): string | null {
  const base = path.split("/").pop() || path;
  if (NAMES[base]) return NAMES[base];
  const dot = base.lastIndexOf(".");
  if (dot < 0) return null;
  return EXT[base.slice(dot + 1).toLowerCase()] ?? null;
}

const NUMBER = String.raw`\b(?:0[xX][0-9a-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b`;
const WORD = String.raw`[A-Za-z_$][\w$]*`;
const compiled = new Map<string, { re: RegExp; keywords: Set<string> }>();

function compile(lang: string) {
  let c = compiled.get(lang);
  if (!c) {
    const l = LANGS[lang];
    const group = (xs: string[]) => (xs.length ? `(${xs.join("|")})` : "(?!)");
    c = {
      re: new RegExp(`${group(l.comment)}|${group(l.string)}|(${NUMBER})|(${WORD})`, "g"),
      keywords: new Set(l.keywords.map((k) => (lang === "sql" ? k.toLowerCase() : k))),
    };
    compiled.set(lang, c);
  }
  return c;
}

/** Tokens for the whole text, split into lines (a multi-line comment or string spans several lines). */
export function highlight(text: string, lang: string | null): Token[][] {
  const flat: Token[] = [];
  if (!lang || !LANGS[lang] || text.length > 400_000) {
    flat.push({ kind: null, text });
  } else {
    const { re, keywords } = compile(lang);
    re.lastIndex = 0;
    let last = 0;
    for (let m = re.exec(text); m; m = re.exec(text)) {
      if (m[0] === "") {
        re.lastIndex++;
        continue;
      }
      let kind: TokenKind | null = null;
      if (m[1] !== undefined) kind = "comment";
      else if (m[2] !== undefined) kind = "string";
      else if (m[3] !== undefined) kind = "number";
      else if (m[4] !== undefined) {
        const w = lang === "sql" ? m[4].toLowerCase() : m[4];
        kind = keywords.has(w) ? "keyword" : null;
      }
      if (m.index > last) flat.push({ kind: null, text: text.slice(last, m.index) });
      flat.push({ kind, text: m[0] });
      last = m.index + m[0].length;
    }
    if (last < text.length) flat.push({ kind: null, text: text.slice(last) });
  }
  const lines: Token[][] = [[]];
  for (const tok of flat) {
    const parts = tok.text.split("\n");
    parts.forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ kind: tok.kind, text: part });
    });
  }
  // A trailing newline ends the last line rather than starting an empty one.
  if (lines.length > 1 && lines[lines.length - 1].length === 0 && text.endsWith("\n")) lines.pop();
  return lines;
}
