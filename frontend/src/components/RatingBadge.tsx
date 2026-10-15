import type { Rating } from "@/api/events";
import { RATING_CLASSES, RATING_LABELS } from "@/lib/evidence";
import { cn } from "@/lib/utils";

/** The overall evidence-quality rating with the model's justification. */
export function RatingBadge({ rating }: { rating: Rating }) {
  return (
    <section aria-label="Evidence quality" className="bg-card rounded-md border p-4">
      <p className="text-sm">
        <span className="text-muted-foreground">Evidence quality: </span>
        <span
          className={cn("rounded border px-1.5 py-0.5 font-semibold", RATING_CLASSES[rating.level])}
        >
          {RATING_LABELS[rating.level]}
        </span>
      </p>
      <p className="text-muted-foreground mt-2 text-sm">{rating.justification}</p>
    </section>
  );
}
