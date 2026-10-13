import { useCallback, useEffect, useState } from "react";

import { getLiveness, getReadiness, type CheckState } from "@/api/client";
import { Button } from "@/components/ui/button";
import { getConfig } from "@/lib/config";
import { cn } from "@/lib/utils";

type RowState = CheckState | "checking";

interface StatusRow {
  label: string;
  state: RowState;
  okText: string;
  failText: string;
}

const STATE_DOT: Record<RowState, string> = {
  ok: "bg-success",
  fail: "bg-destructive",
  checking: "bg-muted-foreground/40 animate-pulse",
};

export default function App() {
  const [api, setApi] = useState<RowState>("checking");
  const [db, setDb] = useState<RowState>("checking");
  const [redis, setRedis] = useState<RowState>("checking");

  const check = useCallback(async () => {
    setApi("checking");
    setDb("checking");
    setRedis("checking");
    const [alive, ready] = await Promise.all([getLiveness(), getReadiness()]);
    setApi(alive ? "ok" : "fail");
    setDb(ready?.checks.database ?? "fail");
    setRedis(ready?.checks.redis ?? "fail");
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  const rows: StatusRow[] = [
    {
      label: "API",
      state: api,
      okText: "Reachable",
      failText: `Not reachable at ${getConfig().apiBaseUrl}`,
    },
    { label: "Database", state: db, okText: "Connected", failText: "Not connected" },
    { label: "Cache", state: redis, okText: "Connected", failText: "Not connected" },
  ];
  const busy = rows.some((r) => r.state === "checking");

  return (
    <div className="flex min-h-dvh flex-col">
      <main className="mx-auto w-full max-w-2xl flex-1 px-6 pt-24 pb-16 sm:pt-32">
        <h1 className="font-serif text-5xl font-semibold tracking-tight sm:text-6xl">
          MedCite
          <sup
            aria-hidden="true"
            className="border-primary text-primary ml-1.5 inline-flex h-7 min-w-7 -translate-y-5 items-center justify-center rounded border px-1.5 font-sans text-sm font-semibold sm:-translate-y-7"
          >
            1
          </sup>
        </h1>
        <p className="text-muted-foreground mt-6 max-w-prose font-serif text-lg leading-relaxed">
          Answers from PubMed literature, with every claim cited and every study labeled by the
          strength of its evidence.
        </p>

        <section aria-labelledby="status-heading" className="mt-16 border-t pt-8">
          <div className="flex items-baseline justify-between gap-4">
            <h2 id="status-heading" className="text-base font-semibold">
              Service status
            </h2>
            <Button variant="outline" size="sm" onClick={() => void check()} disabled={busy}>
              Check again
            </Button>
          </div>
          <ul aria-live="polite" className="mt-4 divide-y">
            {rows.map((row) => (
              <li key={row.label} className="flex items-center gap-3 py-3 text-sm">
                <span
                  aria-hidden="true"
                  className={cn("size-2 rounded-full", STATE_DOT[row.state])}
                />
                <span className="w-20 font-medium">{row.label}</span>
                <span className="text-muted-foreground">
                  {row.state === "checking"
                    ? "Checking"
                    : row.state === "ok"
                      ? row.okText
                      : row.failText}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground mt-6 text-sm">
            Search opens here once the evidence pipeline is in place.
          </p>
        </section>
      </main>

      <footer className="text-muted-foreground border-t px-6 py-6 text-xs">
        <p className="mx-auto max-w-2xl">
          Data from PubMed, courtesy of the U.S. National Library of Medicine. MedCite summarizes
          published research and is not medical advice.
        </p>
      </footer>
    </div>
  );
}
