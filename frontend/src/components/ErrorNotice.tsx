import { Button } from "@/components/ui/button";
import type { StreamError } from "@/stream/reducer";

const NOT_RETRYABLE = new Set([401, 422, 429]);

/** Rounded up to the minute: Retry-After counts to midnight, which would read as 7:59. */
function resetTime(seconds: number): string {
  const minute = 60_000;
  const at = Math.ceil((Date.now() + seconds * 1000) / minute) * minute;
  return new Date(at).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ErrorNotice({ error, onRetry }: { error: StreamError; onRetry: () => void }) {
  const retryable = error.status === null || !NOT_RETRYABLE.has(error.status);
  return (
    <div
      role="alert"
      className="border-destructive/50 bg-destructive/5 rounded-md border p-4 text-sm"
    >
      <p className="font-medium">{error.message}</p>
      {error.retryAfterS !== null ? (
        <p className="mt-1">It resets at {resetTime(error.retryAfterS)} your time.</p>
      ) : null}
      {error.traceId ? (
        <p className="text-muted-foreground mt-1 text-xs">
          Reference: <code>{error.traceId}</code>
        </p>
      ) : null}
      {retryable ? (
        <Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}
