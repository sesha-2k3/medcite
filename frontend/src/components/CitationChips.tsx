import type { Citation } from "@/api/events";
import { useCitations } from "@/citations/context";
import { sectionLabel } from "@/lib/evidence";
import { cn } from "@/lib/utils";

function chipLabel(marker: string, citation: Citation | undefined): string {
  if (!citation) return `Source ${marker}, not loaded yet`;
  return `Source ${marker}: PMID ${citation.pmid}, ${sectionLabel(citation.section)}`;
}

/** Buttons for one marker group; activating one shows that passage in the source panel. */
export function CitationChips({ markers }: { markers: string[] }) {
  const { citations, selected, select } = useCitations();
  return (
    <span className="mx-0.5 inline-flex gap-0.5 align-baseline">
      {markers.map((marker, index) => {
        const citation = citations[marker];
        return (
          <button
            key={`${marker}-${index}`}
            type="button"
            disabled={!citation}
            aria-label={chipLabel(marker, citation)}
            aria-pressed={selected === marker}
            onClick={() => select(marker)}
            className={cn(
              "border-primary/40 text-primary rounded border px-1 font-sans text-[0.7rem] leading-4 font-semibold",
              "hover:bg-accent disabled:cursor-default disabled:opacity-50",
              selected === marker && "bg-primary text-primary-foreground hover:bg-primary",
            )}
          >
            {marker}
          </button>
        );
      })}
    </span>
  );
}
