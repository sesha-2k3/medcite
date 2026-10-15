import { Check, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import type { Step } from "@/api/events";
import { Button } from "@/components/ui/button";

const WAIT_HINTS = {
  ask: "Answers usually take 15 seconds to a minute. MedCite waits on the model provider's rate limit, so long pauses are normal.",
  discover:
    "Digests usually take one to two minutes. MedCite waits on the model provider's rate limit, so long pauses are normal.",
};

interface Props {
  steps: Step[];
  active: boolean;
  kind: "ask" | "discover";
  startedAt: number;
  finishedAt: number | null;
  onStop: () => void;
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

function StepList({ steps, active }: { steps: Step[]; active: boolean }) {
  return (
    <ol className="mt-3 space-y-1.5 text-sm">
      {steps.map((step, index) => {
        const current = active && index === steps.length - 1;
        return (
          <li key={index} className="flex items-start gap-2">
            {current ? (
              <LoaderCircle aria-hidden className="text-primary mt-0.5 size-4 animate-spin" />
            ) : (
              <Check aria-hidden className="text-success mt-0.5 size-4" />
            )}
            <span className={current ? "font-medium" : "text-muted-foreground"}>
              {step.label}
              {step.detail ? <span className="text-muted-foreground"> · {step.detail}</span> : null}
            </span>
          </li>
        );
      })}
      {active && steps.length === 0 ? (
        <li className="text-muted-foreground flex items-center gap-2">
          <LoaderCircle aria-hidden className="size-4 animate-spin" />
          Connecting
        </li>
      ) : null}
    </ol>
  );
}

/** Pipeline steps as they happen, announced to screen readers; collapsed once finished. */
export function StepProgress({ steps, active, kind, startedAt, finishedAt, onStop }: Props) {
  const now = useNow(active);
  const seconds = Math.max(0, Math.round(((finishedAt ?? now) - startedAt) / 1000));
  const current = steps.at(-1);
  const announcement = active ? (current?.label ?? "Connecting") : "Finished";

  return (
    <section aria-label="Progress" className="bg-card rounded-md border p-4">
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>
      {active ? (
        <>
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-sm font-semibold">Working on it</h2>
            <div className="flex items-center gap-3">
              <span className="text-muted-foreground text-xs tabular-nums">{seconds} s</span>
              <Button variant="outline" size="sm" onClick={onStop}>
                Stop
              </Button>
            </div>
          </div>
          <StepList steps={steps} active />
          <p className="text-muted-foreground mt-3 text-xs">{WAIT_HINTS[kind]}</p>
        </>
      ) : (
        <details>
          <summary className="cursor-pointer text-sm font-medium">
            Finished in {seconds} s ({steps.length} {steps.length === 1 ? "step" : "steps"})
          </summary>
          <StepList steps={steps} active={false} />
        </details>
      )}
    </section>
  );
}
