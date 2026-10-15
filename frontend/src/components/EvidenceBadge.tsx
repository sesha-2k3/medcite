import { badgeClass } from "@/lib/evidence";
import { cn } from "@/lib/utils";

/** A study's evidence level: color always paired with its text label. */
export function EvidenceBadge({ badge, className }: { badge: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 font-sans text-xs font-medium whitespace-nowrap",
        badgeClass(badge),
        className,
      )}
    >
      {badge}
    </span>
  );
}
