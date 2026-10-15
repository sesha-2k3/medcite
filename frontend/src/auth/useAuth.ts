/** The one way components learn who is signed in; a Supabase provider would plug in here. */
import { useAuthStore } from "@/auth/store";
import { getConfig, type AuthProvider } from "@/lib/config";

export interface Auth {
  provider: AuthProvider;
  token: string | null;
  expiresAt: number | null;
  reason: string | null;
  signIn: (raw: string) => void;
  signOut: (reason?: string | null) => void;
}

export function useAuth(): Auth {
  const token = useAuthStore((s) => s.token);
  const expiresAt = useAuthStore((s) => s.expiresAt);
  const reason = useAuthStore((s) => s.reason);
  const signIn = useAuthStore((s) => s.signIn);
  const signOut = useAuthStore((s) => s.signOut);
  return { provider: getConfig().authProvider, token, expiresAt, reason, signIn, signOut };
}
