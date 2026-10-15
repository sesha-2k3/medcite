import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getArticle } from "@/api/client";
import type { Citation } from "@/api/events";
import { useCitations } from "@/citations/context";
import { EvidenceBadge } from "@/components/EvidenceBadge";
import { designLabel, pubmedUrl, sectionLabel } from "@/lib/evidence";
import { markerNumber } from "@/lib/markers";
import { cn } from "@/lib/utils";

export interface ArticleInfo {
  title: string;
  badge: string;
  year: number | null;
}

function FullRecord({ pmid }: { pmid: number }) {
  const query = useQuery({
    queryKey: ["article", pmid],
    queryFn: () => getArticle(pmid),
    staleTime: Infinity,
  });
  if (query.isPending) return <p className="text-muted-foreground mt-3 text-sm">Loading</p>;
  if (query.isError) {
    return (
      <p role="alert" className="text-destructive mt-3 text-sm">
        {query.error.message}
      </p>
    );
  }
  const article = query.data;
  const facts = [article.journal, article.pub_date, designLabel(article.design)].filter(Boolean);
  return (
    <div className="mt-3 space-y-3 border-t pt-3 text-sm">
      {article.is_retracted ? (
        <p className="text-destructive font-semibold">This article has been retracted.</p>
      ) : null}
      <p className="text-muted-foreground text-xs">{facts.join(" · ")}</p>
      {article.passages
        .filter((passage) => passage.section !== "Title")
        .map((passage) => (
          <div key={passage.chunk_id}>
            <h4 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
              {sectionLabel(passage.section)}
            </h4>
            <p className="mt-1 leading-relaxed">{passage.content}</p>
          </div>
        ))}
    </div>
  );
}

interface CardProps {
  citation: Citation;
  article: ArticleInfo | undefined;
  selected: boolean;
  selectionKey: number;
}

function SourceCard({ citation, article, selected, selectionKey }: CardProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!selected) return;
    headingRef.current?.scrollIntoView?.({ block: "nearest" });
    headingRef.current?.focus();
  }, [selected, selectionKey]);

  return (
    <li>
      <article
        aria-current={selected ? "true" : undefined}
        className={cn(
          "bg-card rounded-md border p-4",
          selected && "border-primary ring-primary ring-1",
        )}
      >
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-primary font-semibold">{citation.marker}</span>
          {article ? <EvidenceBadge badge={article.badge} /> : null}
          <span className="text-muted-foreground">{sectionLabel(citation.section)}</span>
          {article?.year ? <span className="text-muted-foreground">{article.year}</span> : null}
        </div>
        <h3
          ref={headingRef}
          tabIndex={-1}
          className="mt-2 font-serif text-base leading-snug font-semibold"
        >
          {article?.title ?? `PMID ${citation.pmid}`}
        </h3>
        <blockquote className="mt-2 border-l-2 pl-3 text-sm leading-relaxed">
          {citation.snippet}
        </blockquote>
        <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
          <a
            href={pubmedUrl(citation.pmid)}
            target="_blank"
            rel="noreferrer"
            className="text-primary inline-flex items-center gap-1 underline-offset-2 hover:underline"
          >
            PubMed {citation.pmid}
            <ExternalLink aria-hidden className="size-3.5" />
          </a>
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((open) => !open)}
            className="text-primary underline-offset-2 hover:underline"
          >
            {expanded ? "Hide full record" : "Show full record"}
          </button>
        </div>
        {expanded ? <FullRecord pmid={citation.pmid} /> : null}
      </article>
    </li>
  );
}

/** Every cited passage, in marker order; the selected one is highlighted and focused. */
export function SourcePanel({ articles }: { articles: Record<number, ArticleInfo> }) {
  const { citations, selected, selectionKey } = useCitations();
  const ordered = Object.values(citations).sort(
    (a, b) => markerNumber(a.marker) - markerNumber(b.marker),
  );
  return (
    <section aria-labelledby="sources-heading">
      <h2 id="sources-heading" className="text-base font-semibold">
        Sources
      </h2>
      {ordered.length === 0 ? (
        <p className="text-muted-foreground mt-2 text-sm">
          Cited passages appear here once the answer arrives. Select a citation such as C1 to see
          the exact passage it rests on.
        </p>
      ) : (
        <ol className="mt-3 space-y-3">
          {ordered.map((citation) => (
            <SourceCard
              key={citation.marker}
              citation={citation}
              article={articles[citation.pmid]}
              selected={selected === citation.marker}
              selectionKey={selectionKey}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
