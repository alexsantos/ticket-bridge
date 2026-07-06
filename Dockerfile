# Dockerfile
# ----------
# Runs anywhere a container can - a plain VM, Cloud Run, etc. (see
# README.md sections 3.5 and 4). A multi-stage build isn't needed here -
# the dependencies are lightweight (FastAPI + psycopg + httpx), so we keep
# a single stage for simplicity. Dependencies are installed with uv from
# the committed uv.lock, for fast and reproducible builds.
#
# The app's own in-process scheduler (app/scheduler.py) triggers outbox
# sync automatically once the container is running - no external cron or
# pinger needed, unless SYNC_SCHEDULER_ENABLED=false (e.g. on Cloud Run,
# see README.md section 4.6).

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# psycopg[binary] already bundles the compiled driver, so libpq-dev isn't needed.
COPY pyproject.toml uv.lock ./
COPY app ./app
RUN uv sync --frozen --no-dev

# Run the interpreter from the venv directly instead of via `uv run`, so
# the container doesn't re-check/re-sync the lockfile (and doesn't need
# network access to PyPI) on every start.
ENV PATH="/app/.venv/bin:${PATH}"

# Cloud Run injects the PORT variable - uvicorn must respect it.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
