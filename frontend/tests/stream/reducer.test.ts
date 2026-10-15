import { describe, expect, it } from "vitest";

import { initialStreamState, streamReducer, type StreamState } from "@/stream/reducer";

import {
  DIGEST_EVENTS,
  PLAN,
  QUESTION_EVENTS,
  reduceEvents,
  type RawEvent,
} from "../fixtures/stream";

const ERROR: RawEvent = [
  "error",
  { code: "upstream_error", message: "Groq failed", trace_id: "t" },
];

function at(index: number): RawEvent {
  const event = QUESTION_EVENTS[index];
  if (!event) throw new Error(`no event ${index}`);
  return event;
}

describe("streamReducer", () => {
  it("moves from connecting to streaming when the stream opens", () => {
    expect(streamReducer(initialStreamState, { type: "open" }).status).toBe("streaming");
  });

  it("applies every question event", () => {
    const state = reduceEvents(QUESTION_EVENTS);
    expect(state.status).toBe("done");
    expect(state.steps.map((s) => s.label)).toEqual([
      "Planning the search",
      "Searching PubMed: exact evidence",
    ]);
    expect(state.steps[1]?.detail).toBe("tier 1");
    expect(state.gap).toEqual({ requestedEvidence: "meta-analysis or systematic review" });
    expect(state.text).toContain("Activation differed slightly [C1].\n\nGrowth");
    expect(Object.keys(state.citations)).toEqual(["C1", "C2"]);
    expect(state.evidenceTable[0]?.markers).toEqual(["C1"]);
    expect(state.rating).toEqual({ level: "very_low", justification: "Only surrogate evidence." });
    expect(state.warnings).toHaveLength(1);
    expect(state.done).toEqual({ traceId: "trace-1", queryId: 7, queryPlan: PLAN });
    expect(state.error).toBeNull();
  });

  it("applies theme events for a digest and accepts a null query id and plan", () => {
    const state = reduceEvents(DIGEST_EVENTS);
    expect(state.themes).toHaveLength(1);
    expect(state.themes[0]?.more_articles).toBe(2);
    expect(state.citations.C1?.pmid).toBe(90000010);
    expect(state.done).toEqual({ traceId: "trace-2", queryId: null, queryPlan: null });
  });

  it("reaches the same state when events arrive out of order", () => {
    const shuffled = [9, 5, 7, 0, 3, 6, 2, 1, 8, 4].map(at);
    expect(reduceEvents(shuffled)).toEqual(reduceEvents(QUESTION_EVENTS));
  });

  it("keeps what arrived and ignores what follows an error at any point", () => {
    for (let index = 0; index < QUESTION_EVENTS.length; index += 1) {
      const before = QUESTION_EVENTS.slice(0, index);
      const state = reduceEvents([...before, ERROR, ...QUESTION_EVENTS.slice(index)]);
      const expected: StreamState = {
        ...reduceEvents(before),
        status: "error",
        error: {
          code: "upstream_error",
          message: "Groq failed",
          traceId: "t",
          status: null,
          retryAfterS: null,
        },
      };
      expect(state).toEqual(expected);
    }
  });

  it("ignores an error event after done", () => {
    expect(reduceEvents([...QUESTION_EVENTS, ERROR]).status).toBe("done");
  });

  it("ignores unknown events and malformed data", () => {
    const start = reduceEvents([]);
    const noise: RawEvent[] = [
      ["mystery", { a: 1 }],
      ["step", { label: "no node" }],
      ["token", "not an object"],
      ["citations", [{ marker: 3 }]],
      ["rating", { level: "excellent", justification: "x" }],
      ["theme", { name: "no summary" }],
      ["warning", {}],
    ];
    const state = reduceEvents(noise, start);
    expect(state.steps).toEqual([]);
    expect(state.text).toBe("");
    expect(state.citations).toEqual({});
    expect(state.rating).toBeNull();
    expect(state.themes).toEqual([]);
    expect(state.warnings).toEqual([]);
  });

  it("does not repeat an identical warning", () => {
    const warning = at(8);
    expect(reduceEvents([warning, warning]).warnings).toHaveLength(1);
  });

  it("reports a refused request with its status and retry time", () => {
    const state = streamReducer(initialStreamState, {
      type: "http_error",
      status: 429,
      body: { error: { code: "rate_limited", message: "daily usage limit", trace_id: "t9" } },
      retryAfterS: 3600,
    });
    expect(state.status).toBe("error");
    expect(state.error).toMatchObject({ code: "rate_limited", status: 429, retryAfterS: 3600 });
  });

  it("treats a stream that closes before done as an error, and a lost connection too", () => {
    const partial = reduceEvents(QUESTION_EVENTS.slice(0, 3));
    expect(streamReducer(partial, { type: "closed" }).error?.code).toBe("incomplete");
    expect(streamReducer(partial, { type: "network_error", message: "x" }).error?.code).toBe(
      "network",
    );
  });

  it("ignores closing and network errors once finished or cancelled", () => {
    const done = reduceEvents(QUESTION_EVENTS);
    expect(streamReducer(done, { type: "closed" })).toBe(done);
    expect(streamReducer(done, { type: "network_error", message: "x" })).toBe(done);
    const cancelled = streamReducer(reduceEvents([]), { type: "cancel" });
    expect(cancelled.status).toBe("cancelled");
    expect(streamReducer(cancelled, { type: "event", name: "token", data: { text: "x" } })).toBe(
      cancelled,
    );
  });
});
