/** URLs for runs. The query string lets a reloaded result page offer to ask again. */
import type { RunRequest } from "@/stores/runs";

export function resultPath(id: string, request: RunRequest): string {
  const params =
    request.kind === "ask"
      ? new URLSearchParams({ q: request.question })
      : new URLSearchParams({ q: request.topic, mode: "discover", months: String(request.months) });
  return `/r/${id}?${params.toString()}`;
}

export function askAgainPath(params: URLSearchParams): string {
  const query = params.toString();
  return query ? `/?${query}` : "/";
}
