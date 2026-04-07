#!/bin/sh
set -eu

exec uvicorn src.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8080}"
