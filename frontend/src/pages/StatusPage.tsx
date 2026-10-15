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

/** Whether the API, database and cache are reachable (the M0 status page). */
export function StatusPage() {
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
    <section aria-labelledby="status-heading" className="mx-auto max-w-2xl pt-8">
      <div className="flex items-baseline justify-between gap-4">
        <h1 id="status-heading" className="font-serif text-2xl font-semibold">
          Service status
        </h1>
        <Button variant="outline" size="sm" onClick={() => void check()} disabled={busy}>
          Check again
        </Button>
      </div>
      <ul aria-live="polite" className="mt-4 divide-y">
        {rows.map((row) => (
          <li key={row.label} className="flex items-center gap-3 py-3 text-sm">
            <span aria-hidden="true" className={cn("size-2 rounded-full", STATE_DOT[row.state])} />
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
    </section>
  );
}
