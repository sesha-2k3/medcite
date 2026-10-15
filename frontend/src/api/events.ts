/**
 * Shapes of the stream's events and of the plan in `done` (handoff 7.9). Written by hand because
 * streamed responses carry no OpenAPI schema; keep them in step with `app/graph/nodes.py`.
 */

export interface Step {
  node: string;
  label: string;
  detail?: string;
}

export interface Citation {
  marker: string;
  pmid: number;
  section: string | null;
  chunk_id: number;
  /** The passage exactly as the model saw it. */
  snippet: string;
}

export interface EvidenceRow {
  pmid: number;
  title: string;
  design: string;
  n: number | null;
  finding: string | null;
  year: number | null;
  badge: string;
  markers: string[];
}

export type RatingLevel = "high" | "moderate" | "low" | "very_low";

export interface Rating {
  level: RatingLevel;
  justification: string;
}

export interface ThemeArticle {
  pmid: number;
  title: string;
  design: string;
  badge: string;
  year: number | null;
  marker: string;
}

export interface Theme {
  name: string;
  summary: string;
  evidence_mix: string;
  articles: ThemeArticle[];
  more_articles: number;
}

export interface Tier {
  level: number;
  purpose: string;
  pubmed_query: string;
}

export interface Plan {
  mode: string;
  standalone_query: string;
  population?: string | null;
  condition?: string | null;
  intervention?: string | null;
  comparison?: string | null;
  outcome?: string | null;
  tiers: Tier[];
  rejected_mesh: string[];
  broadened: boolean;
  source?: "llm" | "fallback";
}

export interface Done {
  traceId: string;
  /** Null when the query log could not be written (it fails open). */
  queryId: number | null;
  /** Null for out-of-scope questions, which skip planning. */
  queryPlan: Plan | null;
}
