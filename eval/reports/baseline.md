# Evaluation report: baseline

1 questions, 1 with gold PMIDs.

| Metric | Value |
|---|---|
| Gold found by PubMed search | 100% |
| Recall@10 (context articles) | 100% |
| MRR | 1.00 |
| Gap accuracy | 100% |
| False gaps | none |
| Missed gaps | none |
| Verification pass rate | 0% |
| Judge unavailable | 0 |
| Answers revised once | 1 |
| Median latency (s) | 86.7 |
| Mean tokens per answer | 30858 |

Tokens by model: openai/gpt-oss-120b 9,870, openai/gpt-oss-20b 16,790, qwen/qwen3.8-27b 4,198

| Id | Kind | Gap expected | Gap reported | Recall@10 | RR | Verified | Tokens |
|---|---|---|---|---|---|---|---|
| protein_resistance | direct | no | no | 100% | 1.00 | no | 30,858 |
