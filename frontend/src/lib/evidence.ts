/** Display helpers for evidence badges, designs, ratings and sections. */
import type { RatingLevel } from "@/api/events";

const BADGE_CLASSES: Record<string, string> = {
  "Synthesis / Guideline": "border-ev-synthesis/60 bg-ev-synthesis/10 text-ev-synthesis",
  RCT: "border-ev-rct/60 bg-ev-rct/10 text-ev-rct",
  Observational: "border-ev-observational/60 bg-ev-observational/10 text-ev-observational",
  Descriptive: "border-ev-descriptive/60 bg-ev-descriptive/10 text-ev-descriptive",
  "Narrative / Opinion": "border-ev-narrative/60 bg-ev-narrative/10 text-ev-narrative",
  Mechanistic: "border-ev-mechanistic/60 bg-ev-mechanistic/10 text-ev-mechanistic",
};
const UNCLASSIFIED = "border-ev-unclassified/60 bg-ev-unclassified/10 text-ev-unclassified";

export function badgeClass(badge: string): string {
  return BADGE_CLASSES[badge] ?? UNCLASSIFIED;
}

export const RATING_LABELS: Record<RatingLevel, string> = {
  high: "High",
  moderate: "Moderate",
  low: "Low",
  very_low: "Very low",
};

export const RATING_CLASSES: Record<RatingLevel, string> = {
  high: "border-success/60 text-success",
  moderate: "border-primary/60 text-primary",
  low: "border-ev-observational/60 text-ev-observational",
  very_low: "border-destructive/60 text-destructive",
};

const DESIGN_LABELS: Record<string, string> = {
  meta_analysis: "Meta-analysis",
  rct: "RCT",
  in_vitro: "In vitro",
  other: "Other design",
};

export function designLabel(design: string): string {
  const label = DESIGN_LABELS[design];
  if (label) return label;
  const words = design.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** `Abstract-RESULTS` becomes "Results"; `Abstract` and `Title` stay as they are. */
export function sectionLabel(section: string | null): string {
  if (!section) return "Abstract";
  const part = section.startsWith("Abstract-") ? section.slice("Abstract-".length) : section;
  return part.charAt(0).toUpperCase() + part.slice(1).toLowerCase();
}

export function pubmedUrl(pmid: number): string {
  return `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`;
}
