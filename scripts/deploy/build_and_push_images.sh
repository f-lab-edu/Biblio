#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID is required}"
REGION="${REGION:-asia-northeast3}"
REPOSITORY="${REPOSITORY:-biblio}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"

REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"

services=(
  "core-api:services/core-api"
  "search-service:services/search-service"
  "managed-embedding-endpoint:services/managed-embedding-endpoint"
  "pipeline-worker:services/pipeline-worker"
  "feedback-ingestion-pipeline:services/feedback-ingestion-pipeline"
  "feedback-loop-pipeline:services/feedback-loop-pipeline"
)

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

for item in "${services[@]}"; do
  name="${item%%:*}"
  context="${item#*:}"
  image="${REGISTRY}/${name}:${IMAGE_TAG}"
  docker build -t "${image}" "${context}"
  docker push "${image}"
  echo "${name}=${image}"
done
