# syntax=docker/dockerfile:1.7
# Multi-arch: docker buildx build --platform linux/amd64,linux/arm64 -t medcite-api ./backend
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# dependencies
FROM ${PYTHON_IMAGE} AS deps
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /srv
COPY pyproject.toml uv.lock ./
# The project has no build backend, so uv installs dependencies only; app/ runs from /srv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# MedCPT weights
FROM deps AS models
ENV PATH=/opt/venv/bin:$PATH \
    HF_HOME=/models
COPY app ./app
RUN python -m app.models.download

# runtime
FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --create-home --uid 10001 app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/models \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
WORKDIR /srv
COPY --from=deps /opt/venv /opt/venv
COPY --from=models --chown=app:app /models /models
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
USER app
EXPOSE 8000
# Render/most hosts inject $PORT. Worker and cron services override this command.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
