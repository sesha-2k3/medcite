import { Link, useParams, useSearchParams } from "react-router";

import { ResultView } from "@/components/ResultView";
import { Button } from "@/components/ui/button";
import { askAgainPath } from "@/lib/routes";
import { useRuns } from "@/stores/runs";

export function ResultPage() {
  const { runId = "" } = useParams();
  const run = useRuns((s) => s.runs[runId]);
  const [params] = useSearchParams();

  if (!run) {
    return (
      <div className="mx-auto max-w-xl py-12">
        <h1 className="font-serif text-2xl font-semibold">This result is no longer here</h1>
        <p className="text-muted-foreground mt-3">
          Results are kept only while the page is open, so reloading clears them. Asking again runs
          a fresh search.
        </p>
        <Button asChild className="mt-6">
          <Link to={askAgainPath(params)}>Ask again</Link>
        </Button>
      </div>
    );
  }
  return <ResultView key={run.id} run={run} />;
}
