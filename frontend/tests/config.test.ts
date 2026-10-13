import { describe, expect, it } from "vitest";

import { readConfig } from "@/lib/config";

describe("readConfig", () => {
  it("reads values and strips trailing slashes from the API URL", () => {
    const cfg = readConfig({ VITE_API_BASE_URL: "http://localhost:8000/", VITE_AUTH_PROVIDER: "dev" });
    expect(cfg).toEqual({ apiBaseUrl: "http://localhost:8000", authProvider: "dev", sentryDsn: null });
  });

  it("defaults the auth provider to dev", () => {
    expect(readConfig({ VITE_API_BASE_URL: "https://api.example.com" }).authProvider).toBe("dev");
  });

  it("fails loudly when the API URL is missing", () => {
    expect(() => readConfig({})).toThrow(/VITE_API_BASE_URL/);
  });

  it("rejects unknown auth providers", () => {
    expect(() => readConfig({ VITE_API_BASE_URL: "x", VITE_AUTH_PROVIDER: "okta" })).toThrow(
      /VITE_AUTH_PROVIDER/,
    );
  });
});
