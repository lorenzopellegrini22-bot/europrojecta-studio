# Backup notturno cifrato di database e allegati (vedi docker/backup.sh).
# Stessa versione di PostgreSQL del database, così pg_dump è compatibile.
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY docker/backup.sh /usr/local/bin/backup.sh
RUN sed -i 's/\r$//' /usr/local/bin/backup.sh && chmod +x /usr/local/bin/backup.sh

CMD ["/usr/local/bin/backup.sh", "ciclo"]
