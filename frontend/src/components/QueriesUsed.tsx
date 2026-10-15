import type { Plan } from "@/api/events";

/** The PubMed searches behind a result, from the plan in the `done` event. */
export function QueriesUsed({ plan }: { plan: Plan | null }) {
  if (!plan || plan.tiers.length === 0) return null;
  return (
    <details className="bg-card rounded-md border p-4 text-sm">
      <summary className="cursor-pointer font-medium">PubMed queries used</summary>
      <div className="mt-3 space-y-3">
        <p>
          <span className="text-muted-foreground">Searched as: </span>
          {plan.standalone_query}
        </p>
        <p className="text-muted-foreground text-xs">
          Searches run in order and stop once enough relevant evidence is found.
        </p>
        {plan.broadened ? (
          <p>
            The first searches found nothing direct, so the terms were broadened once before
            concluding that evidence was missing.
          </p>
        ) : null}
        {plan.source === "fallback" ? (
          <p>The search planner was unavailable, so a keyword search was used.</p>
        ) : null}
        <ol className="space-y-3">
          {plan.tiers.map((tier) => (
            <li key={tier.level}>
              <p className="font-medium">
                Tier {tier.level}: {tier.purpose}
              </p>
              <pre className="bg-muted mt-1 overflow-x-auto rounded p-2 text-xs break-words whitespace-pre-wrap">
                <code>{tier.pubmed_query}</code>
              </pre>
            </li>
          ))}
        </ol>
        {plan.rejected_mesh.length > 0 ? (
          <p>
            <span className="text-muted-foreground">
              Proposed MeSH headings that don't exist, searched as plain text instead:{" "}
            </span>
            {plan.rejected_mesh.join(", ")}
          </p>
        ) : null}
      </div>
    </details>
  );
}
