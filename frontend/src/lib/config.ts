/**
 * Runtime configuration, read only from VITE_* build-time variables (portability rule 9).
 * Rehosting the frontend means rebuilding with new values; no backend URL lives in source.
 */

export type AuthProvider = "dev" | "supabase";

export interface AppConfig {
  apiBaseUrl: string;
  authProvider: AuthProvider;
  /** A `make dev-token` token to start signed in with; honored only by the dev provider. */
  devToken: string | null;
  sentryDsn: string | null;
}

const AUTH_PROVIDERS: readonly AuthProvider[] = ["dev", "supabase"];

export interface EnvSource {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_AUTH_PROVIDER?: string;
  readonly VITE_DEV_TOKEN?: string;
  readonly VITE_SENTRY_DSN?: string;
}

export function readConfig(env: EnvSource): AppConfig {
  const apiBaseUrl = env.VITE_API_BASE_URL?.trim();
  if (!apiBaseUrl) {
    throw new Error("VITE_API_BASE_URL is not set. Copy frontend/.env.example to frontend/.env.");
  }
  const provider = (env.VITE_AUTH_PROVIDER?.trim() || "dev") as AuthProvider;
  if (!AUTH_PROVIDERS.includes(provider)) {
    throw new Error(`VITE_AUTH_PROVIDER must be one of: ${AUTH_PROVIDERS.join(", ")}`);
  }
  return {
    apiBaseUrl: apiBaseUrl.replace(/\/+$/, ""),
    authProvider: provider,
    devToken: provider === "dev" ? env.VITE_DEV_TOKEN?.trim() || null : null,
    sentryDsn: env.VITE_SENTRY_DSN?.trim() || null,
  };
}

let cached: AppConfig | null = null;

/** The app's config, validated on first use (importing this module has no side effects). */
export function getConfig(): AppConfig {
  cached ??= readConfig(import.meta.env);
  return cached;
}
