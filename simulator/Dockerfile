# Dockerfile - Ticket Bridge simulator (see README.md). Same layout as the
# bridge's own Dockerfile: one stage, dependencies from the committed
# uv.lock, interpreter run straight from the venv.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY sim ./sim

ENV PATH="/app/.venv/bin:${PATH}"
# SQLite database lives here - mount a volume to keep tickets/config across restarts.
ENV SIM_DATA_DIR=/data
ENV PORT=8090
EXPOSE 8090

CMD ["sh", "-c", "uvicorn sim.main:app --host 0.0.0.0 --port ${PORT}"]
