import { useEffect, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "@/auth/useAuth";

const MAX_TIMEOUT_MS = 2 ** 31 - 1;

/** Sends signed-out visitors to sign-in, and signs out when the token expires. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { token, expiresAt, signOut } = useAuth();
  const location = useLocation();

  useEffect(() => {
    if (expiresAt === null) return;
    const delay = Math.min(Math.max(0, expiresAt - Date.now()), MAX_TIMEOUT_MS);
    const id = window.setTimeout(() => signOut("Your session expired. Sign in again."), delay);
    return () => window.clearTimeout(id);
  }, [expiresAt, signOut]);

  if (!token) {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname + location.search }} />;
  }
  return children;
}
