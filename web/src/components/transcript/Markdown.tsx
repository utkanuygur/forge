import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { ALIASES, LANGUAGES } from "./highlight";

const components: Components = {
  a: ({ node: _n, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
};

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
 * loaded), and react-markdown's default URL transform strips javascript: and similar links.
 */
function MarkdownImpl({ text }: { text: string }) {
  return (
    <div className="tx-md" data-testid="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkHtmlAsText]}
        rehypePlugins={[[rehypeHighlight, { languages: LANGUAGES, aliases: ALIASES, detect: false }]]}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownImpl);
