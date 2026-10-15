import { describe, expect, it } from "vitest";

import { decodeExpiry } from "@/auth/jwt";
import { useAuthStore } from "@/auth/store";

function token(claims: object): string {
  const payload = btoa(JSON.stringify(claims)).replace(/=+$/, "").replace(/\+/g, "-");
  return `eyJhbGciOiJIUzI1NiJ9.${payload.replace(/\//g, "_")}.signature`;
}

describe("decodeExpiry", () => {
  it("reads exp in milliseconds", () => {
    expect(decodeExpiry(token({ exp: 2_000_000_000 }))).toBe(2_000_000_000_000);
  });

  it("returns null for anything that is not a JWT with exp", () => {
    expect(decodeExpiry("not-a-token")).toBeNull();
    expect(decodeExpiry("a.%%%.c")).toBeNull();
    expect(decodeExpiry(token({ sub: "x" }))).toBeNull();
  });
});

describe("signIn", () => {
  it("accepts a pasted token with a Bearer prefix and stores it", () => {
    const valid = token({ exp: Math.floor(Date.now() / 1000) + 3600 });
    useAuthStore.getState().signIn(`Bearer ${valid}\n`);
    expect(useAuthStore.getState().token).toBe(valid);
    expect(localStorage.getItem("medcite.devToken")).toBe(valid);
  });

  it("rejects expired and malformed tokens", () => {
    const expired = token({ exp: Math.floor(Date.now() / 1000) - 10 });
    expect(() => useAuthStore.getState().signIn(expired)).toThrow(/expired/);
    expect(() => useAuthStore.getState().signIn("hello")).toThrow(/make dev-token/);
  });
});
