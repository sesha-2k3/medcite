/**
 * Runs: one per submitted question or topic, each holding its stream state. A run is started by
 * the submit action, never by a page effect, so StrictMode's double mount can't start a second
 * pipeline. Runs live in memory only; a reload loses them on purpose rather than re-running.
 */
import { create } from "zustand";

import type { AskBody, DiscoverBody } from "@/api/client";
import { openStream } from "@/api/stream";
import {
  initialStreamState,
  isActive,
  streamReducer,
  type StreamAction,
  type StreamState,
} from "@/stream/reducer";

export type RunRequest =
  | { kind: "ask"; question: string; history: string[] }
  | { kind: "discover"; topic: string; months: number };

export interface Run {
  id: string;
  request: RunRequest;
  /** The run this one follows up on, if any. */
  parentId: string | null;
  startedAt: number;
  finishedAt: number | null;
  state: StreamState;
}

interface RunsState {
  runs: Record<string, Run>;
  start: (request: RunRequest, parentId?: string | null) => string;
  cancel: (id: string) => void;
}

/** The API accepts at most five earlier questions. */
export const MAX_HISTORY = 5;

export function runText(request: RunRequest): string {
  return request.kind === "ask" ? request.question : request.topic;
}

/** History for a follow-up: the parent's own history, then the parent's question or topic. */
export function followUpHistory(parent: RunRequest): string[] {
  const earlier = parent.kind === "ask" ? [...parent.history, parent.question] : [parent.topic];
  return earlier.slice(-MAX_HISTORY);
}

function endpoint(request: RunRequest): { path: string; body: AskBody | DiscoverBody } {
  return request.kind === "ask"
    ? { path: "/v1/ask", body: { question: request.question, history: request.history } }
    : { path: "/v1/discover", body: { topic: request.topic, months: request.months } };
}

const controllers = new Map<string, AbortController>();

export const useRuns = create<RunsState>()((set, get) => {
  const dispatch = (id: string, action: StreamAction): void => {
    set((current) => {
      const run = current.runs[id];
      if (!run) return current;
      const state = streamReducer(run.state, action);
      if (state === run.state) return current;
      const finishedAt = run.finishedAt ?? (isActive(state.status) ? null : Date.now());
      return { runs: { ...current.runs, [id]: { ...run, state, finishedAt } } };
    });
    if (!isActive(get().runs[id]?.state.status ?? "done")) controllers.delete(id);
  };

  return {
    runs: {},
    start(request, parentId = null) {
      // One pipeline at a time: two would compete for the same per-minute token limit.
      for (const [id, controller] of controllers) {
        controller.abort();
        dispatch(id, { type: "cancel" });
      }
      const id = crypto.randomUUID();
      const controller = new AbortController();
      controllers.set(id, controller);
      const run: Run = {
        id,
        request,
        parentId,
        startedAt: Date.now(),
        finishedAt: null,
        state: initialStreamState,
      };
      set((current) => ({ runs: { ...current.runs, [id]: run } }));
      const { path, body } = endpoint(request);
      void openStream(path, body, (action) => dispatch(id, action), { signal: controller.signal });
      return id;
    },
    cancel(id) {
      controllers.get(id)?.abort();
      dispatch(id, { type: "cancel" });
    },
  };
});
