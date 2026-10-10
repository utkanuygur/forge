import { memo, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { ALIASES, LANGUAGES } from "./highlight";

const components: Components = {
  a: ({ node: _n, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
};

/** An app-relative path such as /agents/x/evals (not //host or a URL). */
export function isAppPath(href: string | undefined): href is string {
  return Boolean(href && href.startsWith("/") && !href.startsWith("//"));
}

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
}

/** Turn raw HTML in markdown into literal text, so `<div>` in prose stays visible but never renders. */
function remarkHtmlAsText() {
  const walk = (node: MdNode) => {
    if (node.type === "html") node.type = "text";
    node.children?.forEach(walk);
  };
  return (tree: MdNode) => walk(tree);
}

/**
 * Model-written markdown. Raw HTML is never rendered (it is shown as text and no raw plugin is
 * loaded), and react-markdown's default URL transform strips javascript: and similar links. With `onNavigate`,
 * app-relative links (/agents/...) navigate inside the app instead of opening a new tab.
 */
function MarkdownImpl({ text, onNavigate }: { text: string; onNavigate?: (href: string) => void }) {
  const comps = useMemo<Components>(
    () =>
      onNavigate
        ? {
            a: ({ node: _n, href, ...props }) =>
              isAppPath(href) ? (
                <a
                  {...props}
                  href={href}
                  onClick={(e) => {
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                    e.preventDefault();
                    onNavigate(href);
                  }}
                />
              ) : (
                <a {...props} href={href} target="_blank" rel="noreferrer noopener" />
              ),
          }
        : components,
    [onNavigate],
  );
  return (
    <div className="tx-md" data-testid="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkHtmlAsText]}
        rehypePlugins={[[rehypeHighlight, { languages: LANGUAGES, aliases: ALIASES, detect: false }]]}
        components={comps}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownImpl);
