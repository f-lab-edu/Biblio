#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID is required}"
REGION="${REGION:-asia-northeast3}"
REPOSITORY="${REPOSITORY:-biblio}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"

REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"

services=(
  "core-api:.:services/core-api/Dockerfile"
  "search-service:.:services/search-service/Dockerfile"
  "frontend:frontend:frontend/Dockerfile"
  "managed-embedding-endpoint:services/managed-embedding-endpoint:services/managed-embedding-endpoint/Dockerfile"
  "pipeline-worker:services/pipeline-worker:services/pipeline-worker/Dockerfile"
  "feedback-ingestion-pipeline:services/feedback-ingestion-pipeline:services/feedback-ingestion-pipeline/Dockerfile"
  "feedback-loop-pipeline:services/feedback-loop-pipeline:services/feedback-loop-pipeline/Dockerfile"
)

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

for item in "${services[@]}"; do
  name="${item%%:*}"
  rest="${item#*:}"
  context="${rest%%:*}"
  dockerfile="${rest#*:}"
  image="${REGISTRY}/${name}:${IMAGE_TAG}"
  docker build --platform linux/amd64 -f "${dockerfile}" -t "${image}" "${context}"
  docker push "${image}"
  echo "${name}=${image}"
done
