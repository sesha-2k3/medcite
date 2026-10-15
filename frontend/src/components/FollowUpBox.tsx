import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import { resultPath } from "@/lib/routes";
import { followUpHistory, useRuns, type Run, type RunRequest } from "@/stores/runs";

/**
 * A follow-up question sent with the earlier question (or topic) as `history`, so the planner
 * can read "what about in women?" in context. Only questions typed here carry history.
 */
export function FollowUpBox({ parent }: { parent: Run }) {
  const [text, setText] = useState("");
  const start = useRuns((s) => s.start);
  const navigate = useNavigate();
  const inputId = useId();
  const hintId = useId();
  const question = text.trim();
  const valid = question.length >= 3 && question.length <= 1000;
  const context = parent.request.kind === "ask" ? "this question" : "this topic";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid) return;
    const request: RunRequest = {
      kind: "ask",
      question,
      history: followUpHistory(parent.request),
    };
    navigate(resultPath(start(request, parent.id), request));
  }

  return (
    <form onSubmit={submit} className="bg-card rounded-md border p-4">
      <label htmlFor={inputId} className="text-sm font-semibold">
        Ask a follow-up
      </label>
      <p id={hintId} className="text-muted-foreground mt-1 text-xs">
        MedCite reads it in the context of {context}, so a question like "What about in women?"
        works.
      </p>
      <div className="mt-3 flex gap-2">
        <input
          id={inputId}
          aria-describedby={hintId}
          value={text}
          maxLength={1000}
          onChange={(event) => setText(event.target.value)}
          className="bg-background min-w-0 flex-1 rounded-md border px-3 py-2 text-sm"
        />
        <Button type="submit" disabled={!valid}>
          Ask
        </Button>
      </div>
    </form>
  );
}
