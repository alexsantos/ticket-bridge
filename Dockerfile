# Dockerfile
# ----------
# Image for deployment on Cloud Run. A multi-stage build isn't needed here -
# the dependencies are lightweight (FastAPI + psycopg + httpx), so we keep
# a single stage for simplicity.

FROM python:3.12-slim

WORKDIR /app

# psycopg[binary] already bundles the compiled driver, so libpq-dev isn't needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Cloud Run injects the PORT variable - uvicorn must respect it.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
