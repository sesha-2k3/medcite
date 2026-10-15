# MedCite evaluation

`datasets/smoke.jsonl` holds 20 questions: direct questions with gold PMIDs, gap questions
where the requested evidence should not exist, and false-gap controls where it exists under
other names. Each row has `id`, `kind` (`direct`, `gap`, `false_gap`), `question`,
`expected_gap`, `gold_pmids` (may be empty) and a `note` saying where the gold came from.

## Running

From the repository root, with `make up` running:

```bash
make eval
```

This runs every question not yet in `reports/baseline.jsonl` through the same LangGraph
pipeline as the API, appending one line per finished question and rewriting
`reports/baseline.json` and `reports/baseline.md` each time. It stops before starting a
question once the run has spent `cap` tokens (default 150,000), and stops at the first error;
running it again resumes. Options:

```bash
make eval report=v2 cap=100000
make eval only=nordic_hamstring,dumbbell_barbell
make eval-compare a=baseline b=v2
```

On Groq's free plan (200K tokens a day per model, 20–30K per answer) a full run takes two to
three days. Change one setting between reports and compare them.

## Metrics

| Metric | Meaning |
|---|---|
| Gold found by PubMed search | Share of gold PMIDs among the search candidates |
| Recall@10, MRR | Gold PMIDs among, and the rank of the first in, the context's articles |
| Gap accuracy | Reported gap equals expected gap; false and missed gaps are listed |
| Verification pass rate | Answers passing our deterministic checks and judge |
| Latency, tokens | Per answer; tokens also per model, for the daily limits |

Retrieval metrics use only rows with gold PMIDs. A second run over stored articles reuses the
PubMed cache (24 hours) and the local index, so compare runs made under the same conditions.

## Checking gold PMIDs

Print the title of every gold PMID in one request:

```bash
ids=$(python3 -c 'import json; print(",".join(str(p) for l in open("eval/datasets/smoke.jsonl") if l.strip() for p in json.loads(l)["gold_pmids"]))')
curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id=$ids" | python3 -c 'import json, sys; r = json.load(sys.stdin)["result"]; [print(u, r[u].get("title", "NOT FOUND")) for u in r["uids"]]'
```
