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

requested_services=("$@")

service_exists() {
  local requested_name="$1"
  local item

  for item in "${services[@]}"; do
    if [[ "${item%%:*}" == "${requested_name}" ]]; then
      return 0
    fi
  done

  return 1
}

should_build() {
  local service_name="$1"
  local requested_name

  if (( ${#requested_services[@]} == 0 )); then
    return 0
  fi

  for requested_name in "${requested_services[@]}"; do
    if [[ "${requested_name}" == "${service_name}" ]]; then
      return 0
    fi
  done

  return 1
}

for requested_name in "${requested_services[@]}"; do
  if ! service_exists "${requested_name}"; then
    echo "Unknown service: ${requested_name}" >&2
    echo "Available services: ${services[*]%%:*}" >&2
    exit 2
  fi
done

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

for item in "${services[@]}"; do
  name="${item%%:*}"
  if ! should_build "${name}"; then
    continue
  fi

  rest="${item#*:}"
  context="${rest%%:*}"
  dockerfile="${rest#*:}"
  image="${REGISTRY}/${name}:${IMAGE_TAG}"
  docker build --platform linux/amd64 -f "${dockerfile}" -t "${image}" "${context}"
  docker push "${image}"
  echo "${name}=${image}"
done
