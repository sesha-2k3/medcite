import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";

import type { EvidenceRow, Theme } from "@/api/events";
import { CitationContext, type CitationSelection } from "@/citations/context";
import { CitedMarkdown } from "@/components/CitedMarkdown";
import { Disclaimer } from "@/components/Disclaimer";
import { ErrorNotice } from "@/components/ErrorNotice";
import { EvidenceTable } from "@/components/EvidenceTable";
import { FeedbackButtons } from "@/components/FeedbackButtons";
import { FollowUpBox } from "@/components/FollowUpBox";
import { GapBanner } from "@/components/GapBanner";
import { QueriesUsed } from "@/components/QueriesUsed";
import { RatingBadge } from "@/components/RatingBadge";
import { ResultLayout, type ResultTab } from "@/components/ResultLayout";
import { SourcePanel, type ArticleInfo } from "@/components/SourcePanel";
import { StepProgress } from "@/components/StepProgress";
import { ThemeCard } from "@/components/ThemeCard";
import { Button } from "@/components/ui/button";
import { Warnings } from "@/components/Warnings";
import { resultPath } from "@/lib/routes";
import { runText, useRuns, type Run } from "@/stores/runs";
import { isActive } from "@/stream/reducer";

/** Titles and badges for the source panel; citations carry only PMIDs. */
function articleIndex(rows: EvidenceRow[], themes: Theme[]): Record<number, ArticleInfo> {
  const index: Record<number, ArticleInfo> = {};
  for (const theme of themes) {
    for (const article of theme.articles) index[article.pmid] = article;
  }
  for (const row of rows) index[row.pmid] = row;
  return index;
}

/** One run's result, answer or digest, rendered from whatever has arrived so far. */
export function ResultView({ run }: { run: Run }) {
  const { request, state } = run;
  const start = useRuns((s) => s.start);
  const cancel = useRuns((s) => s.cancel);
  const parent = useRuns((s) => (run.parentId ? s.runs[run.parentId] : undefined));
  const navigate = useNavigate();
  const [tab, setTab] = useState<ResultTab>("answer");
  const [selection, setSelection] = useState({ marker: null as string | null, key: 0 });
  const active = isActive(state.status);

  const select = useCallback((marker: string) => {
    setSelection((current) => ({ marker, key: current.key + 1 }));
    setTab("sources");
  }, []);
  const citationContext = useMemo<CitationSelection>(
    () => ({
      citations: state.citations,
      selected: selection.marker,
      selectionKey: selection.key,
      select,
    }),
    [state.citations, selection, select],
  );
  const articles = useMemo(
    () => articleIndex(state.evidenceTable, state.themes),
    [state.evidenceTable, state.themes],
  );

  const runAgain = () => navigate(resultPath(start(request, run.parentId), request));

  const main = (
    <div className="space-y-6">
      <header>
        {parent ? (
          <p className="text-muted-foreground mb-1 text-xs">
            Follow-up to{" "}
            <Link
              to={resultPath(parent.id, parent.request)}
              className="text-primary underline-offset-2 hover:underline"
            >
              {runText(parent.request)}
            </Link>
          </p>
        ) : null}
        <p className="text-muted-foreground text-xs tracking-wide uppercase">
          {request.kind === "discover"
            ? `Recent research, last ${request.months} ${request.months === 1 ? "month" : "months"}`
            : "Question"}
        </p>
        <h1 className="mt-1 font-serif text-2xl leading-tight font-semibold sm:text-3xl">
          {runText(request)}
        </h1>
      </header>

      <StepProgress
        steps={state.steps}
        active={active}
        kind={request.kind}
        startedAt={run.startedAt}
        finishedAt={run.finishedAt}
        onStop={() => cancel(run.id)}
      />

      {state.gap ? <GapBanner requestedEvidence={state.gap.requestedEvidence} /> : null}

      {state.text ? (
        <section aria-label="Answer">
          <CitedMarkdown
            text={state.text}
            className="text-[1.05rem] [&>p:first-child]:text-lg [&>p:first-child]:font-medium"
          />
        </section>
      ) : null}

      {state.themes.length > 0 ? (
        <section aria-labelledby="themes-heading" className="space-y-4">
          <h2 id="themes-heading" className="text-base font-semibold">
            Themes
          </h2>
          {state.themes.map((theme, index) => (
            <ThemeCard key={`${index}-${theme.name}`} theme={theme} index={index} />
          ))}
        </section>
      ) : null}

      {state.rating ? <RatingBadge rating={state.rating} /> : null}
      <EvidenceTable rows={state.evidenceTable} />
      <Warnings messages={state.warnings} />

      {state.error ? <ErrorNotice error={state.error} onRetry={runAgain} /> : null}
      {state.status === "cancelled" ? (
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">Stopped.</span>
          <Button variant="outline" size="sm" onClick={runAgain}>
            Run again
          </Button>
        </div>
      ) : null}

      {state.done ? (
        <>
          <FeedbackButtons queryId={state.done.queryId} />
          <QueriesUsed plan={state.done.queryPlan} />
        </>
      ) : null}
      {state.status === "done" ? <FollowUpBox parent={run} /> : null}
      <Disclaimer />
    </div>
  );

  return (
    <CitationContext value={citationContext}>
      <ResultLayout
        tab={tab}
        onTab={setTab}
        sourceCount={Object.keys(state.citations).length}
        main={main}
        sources={<SourcePanel articles={articles} />}
      />
    </CitationContext>
  );
}
