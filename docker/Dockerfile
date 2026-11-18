# Immagine unica per app e worker (cambia solo il comando).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# Prima le dipendenze (strato in cache), poi il codice.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

RUN useradd --system --uid 10001 europrojecta \
    && mkdir -p /srv/dati/allegati /srv/dati/documenti_bandi \
    && chown -R europrojecta /srv/dati
ENV CARTELLA_ALLEGATI=/srv/dati/allegati
USER europrojecta

EXPOSE 8000
# --proxy-headers: dietro Caddy l'IP reale del client arriva in X-Forwarded-For
# (la porta 8000 non è esposta su Internet, solo a Caddy)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
