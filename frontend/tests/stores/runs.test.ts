import { describe, expect, it } from "vitest";

import { askAgainPath, resultPath } from "@/lib/routes";
import { followUpHistory, MAX_HISTORY } from "@/stores/runs";

describe("followUpHistory", () => {
  it("appends the parent question to the parent's own history", () => {
    expect(
      followUpHistory({ kind: "ask", question: "What about in women?", history: ["Creatine?"] }),
    ).toEqual(["Creatine?", "What about in women?"]);
  });

  it("keeps only the most recent questions the API accepts", () => {
    const history = ["q1", "q2", "q3", "q4", "q5"];
    const next = followUpHistory({ kind: "ask", question: "q6", history });
    expect(next).toHaveLength(MAX_HISTORY);
    expect(next.at(-1)).toBe("q6");
    expect(next[0]).toBe("q2");
  });

  it("uses a digest's topic as the context for its follow-ups", () => {
    expect(
      followUpHistory({ kind: "discover", topic: "diabetes in older adults", months: 6 }),
    ).toEqual(["diabetes in older adults"]);
  });
});

describe("routes", () => {
  it("puts the request in the result URL so a reload can offer to ask again", () => {
    const path = resultPath("r1", { kind: "discover", topic: "sleep", months: 3 });
    expect(path).toBe("/r/r1?q=sleep&mode=discover&months=3");
    const params = new URL(path, "http://x").searchParams;
    expect(askAgainPath(params)).toBe("/?q=sleep&mode=discover&months=3");
  });
});
