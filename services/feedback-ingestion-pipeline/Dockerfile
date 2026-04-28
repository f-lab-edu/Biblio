FROM timberio/vector:0.54.0-debian

WORKDIR /etc/vector/feedback-ingestion-pipeline

COPY config ./config
COPY fixtures ./fixtures

CMD ["--config", "config/common/transforms.yaml", "--config", "config/production/source_http.yaml", "--config", "config/production/sinks_gcs.yaml"]
