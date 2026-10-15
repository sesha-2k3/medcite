import { SearchX } from "lucide-react";

/** States first and plainly that the requested evidence was not found. */
export function GapBanner({ requestedEvidence }: { requestedEvidence: string }) {
  return (
    <section
      aria-label="Evidence gap"
      className="border-ev-observational/50 bg-ev-observational/10 flex gap-3 rounded-md border p-4"
    >
      <SearchX aria-hidden className="text-ev-observational mt-0.5 size-5 shrink-0" />
      <div className="text-sm">
        <p className="font-semibold">No {requestedEvidence} was found for this question.</p>
        <p className="text-muted-foreground mt-1">
          The answer below draws on the closest indirect evidence MedCite could find in PubMed.
        </p>
      </div>
    </section>
  );
}
