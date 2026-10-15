/**
 * Server-Sent Events over POST for `/v1/ask` and `/v1/discover`, turned into reducer actions.
 *
 * fetch-event-source retries on errors and reconnects when a hidden tab becomes visible; either
 * would re-POST and run the whole pipeline again, so both are switched off.
 */
import { fetchEventSource, type EventSourceMessage } from "@microsoft/fetch-event-source";

import { authHeaders, SESSION_EXPIRED, type ApiErrorBody } from "@/api/client";
import { handleUnauthorized } from "@/auth/store";
import { getConfig } from "@/lib/config";
import type { StreamAction } from "@/stream/reducer";

class HttpFailure extends Error {}

/** A `: ping` comment reaches here as an empty message; it and unparsable data give null. */
export function toAction(message: EventSourceMessage): StreamAction | null {
  if (!message.event) return null;
  let data: unknown = null;
  if (message.data) {
    try {
      data = JSON.parse(message.data);
    } catch {
      return null;
    }
  }
  return { type: "event", name: message.event, data };
}

export function retryAfterSeconds(value: string | null): number | null {
  if (!value) return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

export interface StreamOptions {
  signal: AbortSignal;
  fetchImpl?: typeof globalThis.fetch;
}

/** Resolves when the stream ends; every outcome, including failures, arrives as an action. */
export async function openStream(
  path: string,
  body: unknown,
  dispatch: (action: StreamAction) => void,
  { signal, fetchImpl }: StreamOptions,
): Promise<void> {
  try {
    await fetchEventSource(`${getConfig().apiBaseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
      signal,
      fetch: fetchImpl,
      openWhenHidden: true,
      async onopen(response) {
        const type = response.headers.get("content-type") ?? "";
        if (response.ok && type.startsWith("text/event-stream")) {
          dispatch({ type: "open" });
          return;
        }
        const errorBody = (await response.json().catch(() => null)) as ApiErrorBody | null;
        dispatch({
          type: "http_error",
          status: response.status,
          body: errorBody,
          retryAfterS: retryAfterSeconds(response.headers.get("retry-after")),
        });
        if (response.status === 401) handleUnauthorized(SESSION_EXPIRED);
        throw new HttpFailure();
      },
      onmessage(message) {
        const action = toAction(message);
        if (action) dispatch(action);
      },
      onerror(error) {
        throw error;
      },
    });
  } catch (error) {
    if (signal.aborted || error instanceof HttpFailure) return;
    dispatch({
      type: "network_error",
      message: error instanceof Error ? error.message : String(error),
    });
    return;
  }
  if (!signal.aborted) dispatch({ type: "closed" });
}
