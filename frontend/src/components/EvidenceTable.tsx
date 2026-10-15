import type { EvidenceRow } from "@/api/events";
import { CitationChips } from "@/components/CitationChips";
import { EvidenceBadge } from "@/components/EvidenceBadge";
import { designLabel, pubmedUrl } from "@/lib/evidence";

/** The cited studies. Every field except the finding comes from stored PubMed data. */
export function EvidenceTable({ rows }: { rows: EvidenceRow[] }) {
  if (rows.length === 0) return null;
  return (
    <section aria-labelledby="evidence-heading">
      <h2 id="evidence-heading" className="mb-2 text-base font-semibold">
        Evidence table
      </h2>
      <div className="bg-card overflow-x-auto rounded-md border">
        <table className="w-full min-w-[42rem] text-left text-sm">
          <thead className="bg-muted/60 text-muted-foreground text-xs">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">
                Study
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Design
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                n
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Year
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Finding
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Cited as
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.pmid} className="border-t align-top">
                <td className="max-w-xs px-3 py-2">
                  <p className="font-medium">{row.title}</p>
                  <a
                    href={pubmedUrl(row.pmid)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary text-xs underline-offset-2 hover:underline"
                  >
                    PMID {row.pmid}
                  </a>
                </td>
                <td className="px-3 py-2">
                  <EvidenceBadge badge={row.badge} />
                  <p className="text-muted-foreground mt-1 text-xs">{designLabel(row.design)}</p>
                </td>
                <td className="px-3 py-2 tabular-nums">{row.n ?? "–"}</td>
                <td className="px-3 py-2 tabular-nums">{row.year ?? "–"}</td>
                <td className="px-3 py-2">{row.finding ?? "–"}</td>
                <td className="px-3 py-2">
                  <CitationChips markers={row.markers} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
