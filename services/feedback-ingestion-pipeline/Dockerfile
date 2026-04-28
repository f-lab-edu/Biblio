FROM timberio/vector:0.54.0-debian

RUN groupadd --system vectoruser \
    && useradd --system --gid vectoruser --create-home --home-dir /home/vectoruser vectoruser

WORKDIR /etc/vector/feedback-ingestion-pipeline

COPY config ./config
COPY fixtures ./fixtures

USER vectoruser

CMD ["--config", "config/common/transforms.yaml", "--config", "config/production/source_http.yaml", "--config", "config/production/sinks_gcs.yaml"]
