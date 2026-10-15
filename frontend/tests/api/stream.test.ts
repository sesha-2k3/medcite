import { describe, expect, it } from "vitest";

import { openStream, retryAfterSeconds, toAction } from "@/api/stream";
import { useAuthStore } from "@/auth/store";
import type { StreamAction } from "@/stream/reducer";

function respond(body: string, init: ResponseInit) {
  return async () => new Response(body, init);
}

async function collect(fetchImpl: () => Promise<Response>): Promise<StreamAction[]> {
  const actions: StreamAction[] = [];
  await openStream("/v1/ask", { question: "q" }, (action) => actions.push(action), {
    signal: new AbortController().signal,
    fetchImpl,
  });
  return actions;
}

const SSE = { status: 200, headers: { "content-type": "text/event-stream" } };

describe("toAction", () => {
  it("ignores the empty message a ': ping' comment produces", () => {
    expect(toAction({ id: "", event: "", data: "" })).toBeNull();
  });

  it("parses named events and drops unparsable data", () => {
    expect(toAction({ id: "", event: "token", data: '{"text":"Hi"}' })).toEqual({
      type: "event",
      name: "token",
      data: { text: "Hi" },
    });
    expect(toAction({ id: "", event: "token", data: "{oops" })).toBeNull();
  });
});

describe("openStream", () => {
  it("dispatches events, skips pings and reports the close", async () => {
    const body = [
      'event: step\ndata: {"node":"plan","label":"Planning the search"}\n\n',
      ": ping\n\n",
      'event: done\ndata: {"trace_id":"t","query_id":1,"query_plan":null}\n\n',
    ].join("");
    const actions = await collect(respond(body, SSE));
    expect(actions.map((a) => (a.type === "event" ? a.name : a.type))).toEqual([
      "open",
      "step",
      "done",
      "closed",
    ]);
  });

  it("turns a refused request into one http_error with the retry time", async () => {
    const body = JSON.stringify({
      error: { code: "rate_limited", message: "limit", trace_id: "t" },
    });
    const actions = await collect(
      respond(body, {
        status: 429,
        headers: { "content-type": "application/json", "retry-after": "3600" },
      }),
    );
    expect(actions).toHaveLength(1);
    expect(actions[0]).toMatchObject({ type: "http_error", status: 429, retryAfterS: 3600 });
  });

  it("signs out on 401", async () => {
    useAuthStore.setState({ token: "a.b.c", expiresAt: Date.now() + 60_000, reason: null });
    await collect(respond("{}", { status: 401, headers: { "content-type": "application/json" } }));
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().reason).toMatch(/expired/);
  });

  it("reports a network failure once, without retrying", async () => {
    let calls = 0;
    const actions = await collect(async () => {
      calls += 1;
      throw new TypeError("Failed to fetch");
    });
    expect(calls).toBe(1);
    expect(actions).toEqual([{ type: "network_error", message: "Failed to fetch" }]);
  });
});

describe("retryAfterSeconds", () => {
  it("reads whole seconds and rejects junk", () => {
    expect(retryAfterSeconds("120")).toBe(120);
    expect(retryAfterSeconds(null)).toBeNull();
    expect(retryAfterSeconds("soon")).toBeNull();
  });
});
