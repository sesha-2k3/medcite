/**
 * Rehype plugin turning citation markers in text into `<sup data-markers="C1,C2">`, which
 * `CitedMarkdown` renders as chips. Code and preformatted text are left alone.
 */
import { splitMarkers } from "@/lib/markers";

interface HastNode {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

const SKIP = new Set(["code", "pre"]);

function transform(node: HastNode): void {
  if (!node.children || (node.tagName && SKIP.has(node.tagName))) return;
  node.children = node.children.flatMap((child): HastNode[] => {
    if (child.type !== "text" || typeof child.value !== "string") {
      transform(child);
      return [child];
    }
    return splitMarkers(child.value).map((part): HastNode =>
      part.kind === "text"
        ? { type: "text", value: part.text }
        : {
            type: "element",
            tagName: "sup",
            properties: { dataMarkers: part.markers.join(",") },
            children: [],
          },
    );
  });
}

export function rehypeCitations() {
  return (tree: object): void => {
    transform(tree as HastNode);
  };
}
