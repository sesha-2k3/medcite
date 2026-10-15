/**
 * The stream reducer: folds `/v1/ask` and `/v1/discover` events into one state.
 *
 * Each event writes only its own field, so arrival order never changes the result. `error` is
 * terminal and keeps whatever had arrived; data that arrives after `done` is still merged.
 * Unknown event names and malformed data are ignored rather than thrown.
 */
import type { ApiErrorBody } from "@/api/client";
import type {
  Citation,
  Done,
  EvidenceRow,
  Plan,
  Rating,
  RatingLevel,
  Step,
  Theme,
} from "@/api/events";
import { normalizeMarker } from "@/lib/markers";

export type RunStatus = "connecting" | "streaming" | "done" | "error" | "cancelled";

export interface StreamError {
  code: string;
  message: string;
  traceId: string;
  /** HTTP status when the request was refused before streaming began. */
  status: number | null;
  retryAfterS: number | null;
}

export interface StreamState {
  status: RunStatus;
  steps: Step[];
  gap: { requestedEvidence: string } | null;
  text: string;
  citations: Record<string, Citation>;
  evidenceTable: EvidenceRow[];
  rating: Rating | null;
  themes: Theme[];
  warnings: string[];
  done: Done | null;
  error: StreamError | null;
}

export type StreamAction =
  | { type: "open" }
  | { type: "event"; name: string; data: unknown }
  | { type: "http_error"; status: number; body: ApiErrorBody | null; retryAfterS: number | null }
  | { type: "network_error"; message: string }
  | { type: "closed" }
  | { type: "cancel" };

export const initialStreamState: StreamState = {
  status: "connecting",
  steps: [],
  gap: null,
  text: "",
  citations: {},
  evidenceTable: [],
  rating: null,
  themes: [],
  warnings: [],
  done: null,
  error: null,
};

export function isActive(status: RunStatus): boolean {
  return status === "connecting" || status === "streaming";
}

const HTTP_MESSAGES: Record<number, string> = {
  401: "Your session expired. Sign in again.",
  429: "You've reached today's usage limit.",
  503: "The answering service isn't running on the server right now.",
};

export function streamReducer(state: StreamState, action: StreamAction): StreamState {
  switch (action.type) {
    case "open":
      return state.status === "connecting" ? { ...state, status: "streaming" } : state;
    case "event":
      return applyEvent(state, action.name, action.data);
    case "http_error":
      if (!isActive(state.status)) return state;
      return {
        ...state,
        status: "error",
        error: {
          code: action.body?.error.code ?? `http_${action.status}`,
          message:
            HTTP_MESSAGES[action.status] ??
            action.body?.error.message ??
            `The request failed with status ${action.status}.`,
          traceId: action.body?.error.trace_id ?? "",
          status: action.status,
          retryAfterS: action.retryAfterS,
        },
      };
    case "network_error":
      return failed(state, "network", "The connection to MedCite was lost.");
    case "closed":
      return failed(state, "incomplete", "The connection closed before the answer was finished.");
    case "cancel":
      return isActive(state.status) ? { ...state, status: "cancelled" } : state;
  }
}

function failed(state: StreamState, code: string, message: string): StreamState {
  if (!isActive(state.status)) return state;
  return {
    ...state,
    status: "error",
    error: { code, message, traceId: "", status: null, retryAfterS: null },
  };
}

function applyEvent(state: StreamState, name: string, data: unknown): StreamState {
  if (state.status === "error" || state.status === "cancelled") return state;
  const live: StreamState =
    state.status === "connecting" ? { ...state, status: "streaming" } : state;
  switch (name) {
    case "step":
      return isStep(data) ? { ...live, steps: [...live.steps, data] } : state;
    case "gap":
      return isRecord(data) && typeof data.requested_evidence === "string"
        ? { ...live, gap: { requestedEvidence: data.requested_evidence } }
        : state;
    case "token":
      return isRecord(data) && typeof data.text === "string"
        ? { ...live, text: live.text + data.text }
        : state;
    case "citations":
      return Array.isArray(data)
        ? { ...live, citations: mergeCitations(live.citations, data) }
        : state;
    case "evidence_table":
      return Array.isArray(data)
        ? { ...live, evidenceTable: data.filter(isEvidenceRow).map(normalizeRow) }
        : state;
    case "rating":
      return isRating(data) ? { ...live, rating: data } : state;
    case "theme":
      return isTheme(data) ? { ...live, themes: [...live.themes, normalizeTheme(data)] } : state;
    case "warning":
      if (!isRecord(data) || typeof data.message !== "string") return state;
      if (live.warnings.includes(data.message)) return live;
      return { ...live, warnings: [...live.warnings, data.message] };
    case "done":
      return isRecord(data) ? { ...live, status: "done", done: toDone(data) } : state;
    case "error":
      if (state.status === "done") return state;
      return {
        ...live,
        status: "error",
        error: {
          code: stringField(data, "code") ?? "error",
          message: stringField(data, "message") ?? "Something went wrong.",
          traceId: stringField(data, "trace_id") ?? "",
          status: null,
          retryAfterS: null,
        },
      };
    default:
      return state;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(value: unknown, key: string): string | null {
  const field = isRecord(value) ? value[key] : undefined;
  return typeof field === "string" ? field : null;
}

function isStep(value: unknown): value is Step {
  return (
    isRecord(value) &&
    typeof value.node === "string" &&
    typeof value.label === "string" &&
    (value.detail === undefined || typeof value.detail === "string")
  );
}

function mergeCitations(
  current: Record<string, Citation>,
  items: unknown[],
): Record<string, Citation> {
  const merged = { ...current };
  for (const item of items) {
    if (!isRecord(item) || typeof item.marker !== "string" || typeof item.pmid !== "number") {
      continue;
    }
    const marker = normalizeMarker(item.marker);
    merged[marker] = {
      marker,
      pmid: item.pmid,
      section: typeof item.section === "string" ? item.section : null,
      chunk_id: typeof item.chunk_id === "number" ? item.chunk_id : 0,
      snippet: typeof item.snippet === "string" ? item.snippet : "",
    };
  }
  return merged;
}

function isEvidenceRow(value: unknown): value is EvidenceRow {
  return isRecord(value) && typeof value.pmid === "number" && typeof value.title === "string";
}

function normalizeRow(row: EvidenceRow): EvidenceRow {
  return {
    ...row,
    markers: Array.isArray(row.markers) ? row.markers.map(normalizeMarker) : [],
  };
}

const RATING_LEVELS: readonly RatingLevel[] = ["high", "moderate", "low", "very_low"];

function isRating(value: unknown): value is Rating {
  return (
    isRecord(value) &&
    RATING_LEVELS.includes(value.level as RatingLevel) &&
    typeof value.justification === "string"
  );
}

function isTheme(value: unknown): value is Theme {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    typeof value.summary === "string" &&
    Array.isArray(value.articles)
  );
}

function normalizeTheme(theme: Theme): Theme {
  return {
    ...theme,
    evidence_mix: typeof theme.evidence_mix === "string" ? theme.evidence_mix : "",
    more_articles: typeof theme.more_articles === "number" ? theme.more_articles : 0,
    articles: theme.articles.map((article) => ({
      ...article,
      marker: normalizeMarker(String(article.marker)),
    })),
  };
}

function toDone(data: Record<string, unknown>): Done {
  return {
    traceId: typeof data.trace_id === "string" ? data.trace_id : "",
    queryId: typeof data.query_id === "number" ? data.query_id : null,
    queryPlan: toPlan(data.query_plan),
  };
}

function toPlan(value: unknown): Plan | null {
  if (!isRecord(value) || !Array.isArray(value.tiers)) return null;
  const plan = value as unknown as Plan;
  return {
    ...plan,
    standalone_query: typeof plan.standalone_query === "string" ? plan.standalone_query : "",
    rejected_mesh: Array.isArray(plan.rejected_mesh) ? plan.rejected_mesh : [],
    broadened: plan.broadened === true,
  };
}
