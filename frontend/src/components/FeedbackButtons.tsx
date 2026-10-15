import { useMutation } from "@tanstack/react-query";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { postFeedback, type FeedbackValue } from "@/api/client";
import { Button } from "@/components/ui/button";

/** Thumbs up or down on an answer; hidden when the answer has no query log to attach to. */
export function FeedbackButtons({ queryId }: { queryId: number | null }) {
  const [sent, setSent] = useState<FeedbackValue | null>(null);
  const mutation = useMutation({
    mutationFn: (value: FeedbackValue) => postFeedback(queryId ?? 0, value),
    onSuccess: (_result, value) => setSent(value),
  });
  if (queryId === null) return null;

  return (
    <div role="group" aria-label="Feedback" className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted-foreground">Was this helpful?</span>
      <Button
        variant="outline"
        size="sm"
        aria-pressed={sent === 1}
        disabled={mutation.isPending}
        onClick={() => mutation.mutate(1)}
      >
        <ThumbsUp aria-hidden />
        Yes
      </Button>
      <Button
        variant="outline"
        size="sm"
        aria-pressed={sent === -1}
        disabled={mutation.isPending}
        onClick={() => mutation.mutate(-1)}
      >
        <ThumbsDown aria-hidden />
        No
      </Button>
      <span role="status" className="text-muted-foreground text-xs">
        {mutation.isError ? "Feedback could not be sent." : sent !== null ? "Thank you." : ""}
      </span>
    </div>
  );
}
