#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi

TOPIC="${TOPIC:-transactions}"
BROKER="${BROKER:-kafka-1:9092,kafka-2:9092,kafka-3:9092}"
PRODUCER_CONTAINER_SERVICE="${PRODUCER_CONTAINER_SERVICE:-kafka-1}"

if [ -z "${1:-}" ]; then
  echo "Usage: ./scripts/produce.sh 'message' [key]"
  exit 1
fi

MESSAGE="$1"
MESSAGE_KEY="${2:-}"

CONTAINER="$(docker compose -f "$ROOT_DIR/docker-compose.yaml" ps -q "$PRODUCER_CONTAINER_SERVICE" | head -n 1)"

if [ -z "$CONTAINER" ]; then
  echo "Kafka container not running for project at $ROOT_DIR"
  docker compose -f "$ROOT_DIR/docker-compose.yaml" ps
  exit 1
fi

if [ -n "$MESSAGE_KEY" ]; then
  printf '%s:%s\n' "$MESSAGE_KEY" "$MESSAGE" | docker exec -i "$CONTAINER" /opt/kafka/bin/kafka-console-producer.sh \
    --bootstrap-server "$BROKER" \
    --topic "$TOPIC" \
    --property parse.key=true \
    --property key.separator=:
else
  printf '%s\n' "$MESSAGE" | docker exec -i "$CONTAINER" /opt/kafka/bin/kafka-console-producer.sh \
    --bootstrap-server "$BROKER" \
    --topic "$TOPIC"
fi
