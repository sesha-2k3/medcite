/**
 * The signed-in token. With the dev provider it comes from `VITE_DEV_TOKEN` or a pasted
 * `make dev-token` token kept in localStorage. Components read it through `useAuth()`.
 */
import { create } from "zustand";

import { decodeExpiry } from "@/auth/jwt";
import { getConfig } from "@/lib/config";

const STORAGE_KEY = "medcite.devToken";

interface Session {
  token: string | null;
  expiresAt: number | null;
  /** Why the user was signed out, shown on the sign-in page. */
  reason: string | null;
}

export interface AuthState extends Session {
  signIn: (raw: string) => void;
  signOut: (reason?: string | null) => void;
}

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStored(token: string | null): void {
  try {
    if (token) localStorage.setItem(STORAGE_KEY, token);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Without storage the token lasts until the page is reloaded.
  }
}

function initialSession(): Session {
  const signedOut: Session = { token: null, expiresAt: null, reason: null };
  try {
    const config = getConfig();
    if (config.authProvider !== "dev") return signedOut;
    for (const candidate of [readStored(), config.devToken]) {
      const expiresAt = candidate ? decodeExpiry(candidate) : null;
      if (candidate && expiresAt !== null && expiresAt > Date.now()) {
        return { token: candidate, expiresAt, reason: null };
      }
    }
  } catch {
    return signedOut;
  }
  return signedOut;
}

export const useAuthStore = create<AuthState>()((set) => ({
  ...initialSession(),
  signIn(raw) {
    const token = raw.trim().replace(/^Bearer\s+/i, "");
    const expiresAt = decodeExpiry(token);
    if (expiresAt === null) {
      throw new Error("Not a MedCite token. Paste the output of make dev-token.");
    }
    if (expiresAt <= Date.now()) {
      throw new Error("That token has expired. Run make dev-token for a new one.");
    }
    writeStored(token);
    set({ token, expiresAt, reason: null });
  },
  signOut(reason = null) {
    writeStored(null);
    set({ token: null, expiresAt: null, reason });
  },
}));

export function getToken(): string | null {
  return useAuthStore.getState().token;
}

export function handleUnauthorized(reason: string): void {
  if (useAuthStore.getState().token) useAuthStore.getState().signOut(reason);
}
