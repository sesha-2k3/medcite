import type { Theme } from "@/api/events";
import { CitationChips } from "@/components/CitationChips";
import { CitedMarkdown } from "@/components/CitedMarkdown";
import { EvidenceBadge } from "@/components/EvidenceBadge";

/** One digest theme: name, cited summary, evidence mix and its badged articles. */
export function ThemeCard({ theme, index }: { theme: Theme; index: number }) {
  const headingId = `theme-${index}`;
  return (
    <article aria-labelledby={headingId} className="bg-card rounded-md border p-5">
      <h3 id={headingId} className="font-serif text-xl leading-snug font-semibold">
        {theme.name}
      </h3>
      {theme.evidence_mix ? (
        <p className="text-muted-foreground mt-1 text-xs">Evidence: {theme.evidence_mix}</p>
      ) : null}
      <CitedMarkdown text={theme.summary} className="mt-3" />
      <ul className="mt-4 space-y-2 border-t pt-3">
        {theme.articles.map((article) => (
          <li key={article.pmid} className="flex flex-wrap items-baseline gap-2 text-sm">
            <CitationChips markers={[article.marker]} />
            <EvidenceBadge badge={article.badge} />
            <span>
              {article.title}
              {article.year ? (
                <span className="text-muted-foreground"> ({article.year})</span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
      {theme.more_articles > 0 ? (
        <p className="text-muted-foreground mt-2 text-xs">
          {theme.more_articles} more related {theme.more_articles === 1 ? "article" : "articles"}{" "}
          not summarized.
        </p>
      ) : null}
    </article>
  );
}
