import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type ResultTab = "answer" | "sources";

interface Props {
  tab: ResultTab;
  onTab: (tab: ResultTab) => void;
  sourceCount: number;
  main: ReactNode;
  sources: ReactNode;
}

/** Answer and sources side by side on wide screens; tabs below the `md` breakpoint. */
export function ResultLayout({ tab, onTab, sourceCount, main, sources }: Props) {
  const tabs: { id: ResultTab; label: string }[] = [
    { id: "answer", label: "Answer" },
    { id: "sources", label: `Sources (${sourceCount})` },
  ];
  return (
    <div>
      <div
        role="tablist"
        aria-label="Result sections"
        className="mb-5 flex gap-1 border-b md:hidden"
      >
        {tabs.map(({ id, label }) => (
          <button
            key={id}
            id={`tab-${id}`}
            type="button"
            role="tab"
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            onClick={() => onTab(id)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
              tab === id
                ? "border-primary text-foreground"
                : "text-muted-foreground hover:text-foreground border-transparent",
            )}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="grid gap-8 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div
          id="panel-answer"
          role="tabpanel"
          aria-labelledby="tab-answer"
          className={cn(tab !== "answer" && "hidden md:block")}
        >
          {main}
        </div>
        <div
          id="panel-sources"
          role="tabpanel"
          aria-labelledby="tab-sources"
          className={cn(
            "md:sticky md:top-6 md:max-h-[calc(100dvh-3rem)] md:self-start md:overflow-y-auto md:pr-1",
            tab !== "sources" && "hidden md:block",
          )}
        >
          {sources}
        </div>
      </div>
    </div>
  );
}
