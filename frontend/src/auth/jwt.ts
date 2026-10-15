/** Reads a JWT's expiry without verifying it; only the API decides whether a token is valid. */

export function decodeExpiry(token: string): number | null {
  const payload = token.split(".")[1];
  if (token.split(".").length !== 3 || !payload) return null;
  try {
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    const claims: unknown = JSON.parse(atob(padded));
    if (typeof claims === "object" && claims !== null && "exp" in claims) {
      return typeof claims.exp === "number" ? claims.exp * 1000 : null;
    }
  } catch {
    return null;
  }
  return null;
}
