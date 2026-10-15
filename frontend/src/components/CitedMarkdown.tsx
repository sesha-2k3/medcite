import Markdown, { type Components } from "react-markdown";

import { CitationChips } from "@/components/CitationChips";
import { rehypeCitations } from "@/lib/rehype-citations";
import { cn } from "@/lib/utils";

const REHYPE_PLUGINS = [rehypeCitations];

const COMPONENTS: Components = {
  sup({ node, children, ...rest }) {
    const markers = node?.properties.dataMarkers;
    if (typeof markers === "string") return <CitationChips markers={markers.split(",")} />;
    return <sup {...rest}>{children}</sup>;
  },
};

/** Model-written markdown with `[C#]` markers rendered as citation chips. Raw HTML stays off. */
export function CitedMarkdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn("prose-medcite", className)}>
      <Markdown rehypePlugins={REHYPE_PLUGINS} components={COMPONENTS}>
        {text}
      </Markdown>
    </div>
  );
}
