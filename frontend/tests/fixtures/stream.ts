/** Event sequences shaped like the live reference scenarios (synthetic PMIDs >= 90,000,000). */
import type { Plan } from "@/api/events";
import { initialStreamState, streamReducer, type StreamState } from "@/stream/reducer";

export type RawEvent = [name: string, data: unknown];

export const PLAN: Plan = {
  mode: "question",
  standalone_query: "dumbbell curl vs barbell curl for biceps muscle growth",
  tiers: [
    { level: 1, purpose: "exact evidence", pubmed_query: '"dumbbell curl"[tiab] AND ...' },
    { level: 2, purpose: "direct primary studies", pubmed_query: '"barbell curl"[tiab] AND ...' },
  ],
  rejected_mesh: ["Biceps Brachii"],
  broadened: true,
  source: "llm",
};

export const QUESTION_EVENTS: RawEvent[] = [
  ["step", { node: "plan", label: "Planning the search" }],
  ["step", { node: "retrieve_tier", label: "Searching PubMed: exact evidence", detail: "tier 1" }],
  ["gap", { requested_evidence: "meta-analysis or systematic review" }],
  ["token", { text: "No meta-analysis or systematic review was found. Activation " }],
  ["token", { text: "differed slightly [C1].\n\nGrowth was not measured [C1, C2]." }],
  [
    "citations",
    [
      {
        marker: "C1",
        pmid: 90000001,
        section: "Abstract-RESULTS",
        chunk_id: 11,
        snippet: "Placeholder results passage.",
      },
      {
        marker: "C2",
        pmid: 90000002,
        section: "Abstract",
        chunk_id: 12,
        snippet: "Placeholder abstract passage.",
      },
    ],
  ],
  [
    "evidence_table",
    [
      {
        pmid: 90000001,
        title: "Electromyographic activity of the biceps in curl variations",
        design: "rct",
        n: 12,
        finding: "Similar activation.",
        year: 2018,
        badge: "RCT",
        markers: ["C1"],
      },
    ],
  ],
  ["rating", { level: "very_low", justification: "Only surrogate evidence." }],
  ["warning", { message: "Some statements could not be verified against the cited passages." }],
  ["done", { trace_id: "trace-1", query_id: 7, query_plan: PLAN }],
];

export const DIGEST_EVENTS: RawEvent[] = [
  ["step", { node: "discover_search", label: "Searching recent PubMed records" }],
  [
    "theme",
    {
      name: "Incretin therapies",
      summary: "Two trials reported weight loss [C1].",
      evidence_mix: "1 RCT",
      articles: [
        {
          pmid: 90000010,
          title: "A trial",
          design: "rct",
          badge: "RCT",
          year: 2026,
          marker: "C1",
        },
      ],
      more_articles: 2,
    },
  ],
  [
    "citations",
    [{ marker: "C1", pmid: 90000010, section: "Abstract-CONCLUSIONS", chunk_id: 5, snippet: "x" }],
  ],
  ["done", { trace_id: "trace-2", query_id: null, query_plan: null }],
];

export function reduceEvents(events: RawEvent[], start: StreamState = initialStreamState) {
  return events.reduce(
    (state, [name, data]) => streamReducer(state, { type: "event", name, data }),
    streamReducer(start, { type: "open" }),
  );
}
