# Dockerfile
# ----------
# Imagem para deploy no Cloud Run. Multi-stage não é necessário aqui - as
# dependências são leves (FastAPI + psycopg + httpx), por isso mantemos
# uma única stage para simplicidade.

FROM python:3.12-slim

WORKDIR /app

# psycopg[binary] já traz o driver compilado, não precisamos de libpq-dev.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Cloud Run injeta a variável PORT - o uvicorn tem de a respeitar.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
