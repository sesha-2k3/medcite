# MedCite

Cited, evidence-graded answers to biomedical questions, using only PubMed and PubMed Central
literature. Every claim links to a specific article and section, every study is labeled by
strength of evidence, and the app says plainly when the evidence you asked for doesn't exist.

Data from PubMed, courtesy of the U.S. National Library of Medicine. MedCite summarizes
published research and is not medical advice.


## Repository layout

```
backend/    FastAPI + LangGraph API (Python 3.12, uv)
frontend/   React 19 + TypeScript + Vite SPA
eval/       Evaluation datasets, runner, reports (M10)
docker-compose.yml   Local Postgres (pgvector) + Redis (+ API container)
```

## Prerequisites

Docker (with Compose), Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22+, an
[NCBI API key](https://account.ncbi.nlm.nih.gov/settings/), and a
[Groq API key](https://console.groq.com/keys) (free plan).

## First-time setup

```bash
cp backend/.env.example backend/.env       # fill in NCBI_API_KEY, NCBI_EMAIL, LLM_API_KEY
cp frontend/.env.example frontend/.env

docker compose up -d db redis

cd backend
uv lock                                    # first time only: creates uv.lock (commit it)
uv sync
uv run python -m app.models.download       # MedCPT weights into HF_HOME (~1.3 GB, once)
uv run uvicorn app.main:app --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm install                                # first time: creates package-lock.json (commit it)
npm run dev                                # http://localhost:5173
```

The placeholder page shows whether the API, database and Redis are reachable.

## Checks

| Task | Command (from `backend/` or `frontend/`) |
|---|---|
| Backend tests | `uv run pytest` (Compose-backed tests skip with a message if db/redis aren't up) |
| Confirm Groq key and model IDs | `uv run pytest -m live tests/test_llm_live.py` |
| Lint / format | `uv run ruff check . && uv run ruff format --check .` |
| Types | `uv run mypy` |
| Frontend types / lint / tests | `npm run typecheck && npm run lint && npm test` |
| API container (multi-arch) | `docker buildx build --platform linux/amd64,linux/arm64 -t medcite-api ./backend` |
| Whole stack in containers | `docker compose up --build` |

## LLM provider: Groq free plan

All LLM calls go through `backend/app/models/llm.py`, which asks for a role, not a model.
Groq is reached through its OpenAI-compatible endpoint, so moving to another provider is a
change to `LLM_PROVIDER`, `LLM_BASE_URL` and `LLM_MODEL_*`.

| Role | Default model | Why |
|---|---|---|
| Planner (routing, PICO, queries, metadata extraction) | `openai/gpt-oss-20b` | Small, fast, good at JSON |
| Generator (answers) | `openai/gpt-oss-120b` | Strongest free model |
| Judge (verification) | `qwen/qwen3.8-27b` | Different model family from the generator, so it doesn't share its blind spots |

Free-plan limits are per model and change without notice; check
[console.groq.com/settings/limits](https://console.groq.com/settings/limits). The binding
constraint is tokens per minute, and reasoning tokens count toward it, which is why reasoning
effort is configurable per role (`LLM_REASONING_*`, default low/medium/low). Groq's free plan
carries no data-privacy commitments, so don't send anything sensitive while developing.

## Portability

Configuration comes only from environment variables (`backend/app/config.py`,
`frontend/src/lib/config.ts`); only standard Postgres + `pgvector` is used; services are
stateless and log JSON to stdout; `GET /healthz` and `GET /readyz` exist for any orchestrator.
See `pubmed-assistant-specs.md` §4 for the full rule set.
