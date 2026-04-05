FROM pgvector/pgvector:pg16

ARG PGMQ_VERSION=v1.10.0

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY initdb/ /docker-entrypoint-initdb.d/

RUN git clone --depth 1 --branch "${PGMQ_VERSION}" https://github.com/pgmq/pgmq.git /tmp/pgmq \
    && cp /tmp/pgmq/pgmq-extension/sql/pgmq.sql /docker-entrypoint-initdb.d/001-pgmq.sql \
    && rm -rf /tmp/pgmq

USER postgres
