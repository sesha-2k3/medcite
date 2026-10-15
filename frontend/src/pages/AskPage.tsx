import { useId, useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { Button } from "@/components/ui/button";
import { resultPath } from "@/lib/routes";
import { cn } from "@/lib/utils";
import { useRuns, type RunRequest } from "@/stores/runs";

type Mode = "ask" | "discover";

const MODES: { value: Mode; label: string }[] = [
  { value: "ask", label: "Question" },
  { value: "discover", label: "Discovery" },
];
const MONTH_OPTIONS = [1, 2, 3, 6, 9, 12, 18, 24];
const DEFAULT_MONTHS = 6;
const EXAMPLES: { mode: Mode; text: string }[] = [
  {
    mode: "ask",
    text: "Is there a meta-analysis of dumbbell curl vs barbell curl for biceps muscle growth?",
  },
  {
    mode: "ask",
    text: "Do Nordic hamstring curls reduce hamstring strain injuries in soccer players?",
  },
  { mode: "discover", text: "diabetes in older adults" },
];
const HINTS: Record<Mode, string> = {
  ask: "Ask a specific question. MedCite says plainly when the evidence you ask for doesn't exist.",
  discover: "Name a topic. MedCite groups recent PubMed research on it into cited themes.",
};

function initialMonths(value: string | null): number {
  const months = Number(value);
  return MONTH_OPTIONS.includes(months) ? months : DEFAULT_MONTHS;
}

export function AskPage() {
  const [params] = useSearchParams();
  const [mode, setMode] = useState<Mode>(params.get("mode") === "discover" ? "discover" : "ask");
  const [text, setText] = useState(params.get("q") ?? "");
  const [months, setMonths] = useState(() => initialMonths(params.get("months")));
  const [showError, setShowError] = useState(false);
  const start = useRuns((s) => s.start);
  const navigate = useNavigate();
  const fieldId = useId();
  const hintId = useId();
  const errorId = useId();
  const monthsId = useId();
  const value = text.trim();
  const valid = value.length >= 3 && value.length <= 1000;

  function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!valid) {
      setShowError(true);
      return;
    }
    const request: RunRequest =
      mode === "ask"
        ? { kind: "ask", question: value, history: [] }
        : { kind: "discover", topic: value, months };
    navigate(resultPath(start(request), request));
  }

  function submitOnModifierEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) submit();
  }

  return (
    <div className="mx-auto max-w-2xl pt-8 sm:pt-16">
      <h1 className="font-serif text-4xl font-semibold tracking-tight sm:text-5xl">MedCite</h1>
      <p className="text-muted-foreground mt-4 max-w-prose font-serif text-lg leading-relaxed">
        Answers from PubMed literature, with every claim cited and every study labeled by the
        strength of its evidence.
      </p>

      <form onSubmit={submit} className="bg-card mt-10 space-y-4 rounded-md border p-5" noValidate>
        <fieldset>
          <legend className="sr-only">Mode</legend>
          <div className="bg-muted inline-flex rounded-md p-0.5">
            {MODES.map((option) => (
              <label
                key={option.value}
                className={cn(
                  "has-[:focus-visible]:ring-ring cursor-pointer rounded px-3 py-1.5 text-sm font-medium has-[:focus-visible]:ring-2",
                  mode === option.value
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <input
                  type="radio"
                  name="mode"
                  value={option.value}
                  checked={mode === option.value}
                  onChange={() => setMode(option.value)}
                  className="sr-only"
                />
                {option.label}
              </label>
            ))}
          </div>
        </fieldset>

        <div>
          <label htmlFor={fieldId} className="text-sm font-semibold">
            {mode === "ask" ? "Your question" : "Topic"}
          </label>
          <p id={hintId} className="text-muted-foreground mt-1 text-xs">
            {HINTS[mode]}
          </p>
          <textarea
            id={fieldId}
            rows={mode === "ask" ? 3 : 1}
            value={text}
            maxLength={1000}
            aria-describedby={showError && !valid ? `${hintId} ${errorId}` : hintId}
            aria-invalid={showError && !valid}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={submitOnModifierEnter}
            className="bg-background mt-2 w-full resize-y rounded-md border px-3 py-2 font-serif text-base"
          />
          {showError && !valid ? (
            <p id={errorId} className="text-destructive mt-1 text-xs">
              Enter at least 3 characters.
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-end justify-between gap-4">
          {mode === "discover" ? (
            <div>
              <label htmlFor={monthsId} className="text-sm font-semibold">
                Published in the last
              </label>
              <select
                id={monthsId}
                value={months}
                onChange={(event) => setMonths(Number(event.target.value))}
                className="bg-background mt-1 block rounded-md border px-2 py-1.5 text-sm"
              >
                {MONTH_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option} {option === 1 ? "month" : "months"}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <span />
          )}
          <Button type="submit" size="lg">
            {mode === "ask" ? "Ask" : "Build digest"}
          </Button>
        </div>
      </form>

      <section aria-labelledby="examples-heading" className="mt-8">
        <h2 id="examples-heading" className="text-muted-foreground text-sm font-semibold">
          Try an example
        </h2>
        <ul className="mt-2 space-y-2">
          {EXAMPLES.map((example) => (
            <li key={example.text}>
              <button
                type="button"
                onClick={() => {
                  setMode(example.mode);
                  setText(example.text);
                  setShowError(false);
                }}
                className="text-primary text-left font-serif underline-offset-2 hover:underline"
              >
                {example.text}
                <span className="text-muted-foreground font-sans text-xs">
                  {" "}
                  ({example.mode === "ask" ? "question" : "discovery"})
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
