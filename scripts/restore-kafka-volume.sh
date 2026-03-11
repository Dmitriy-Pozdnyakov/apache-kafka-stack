#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
CLEAR_VOLUME_BEFORE_RESTORE="${CLEAR_VOLUME_BEFORE_RESTORE:-true}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$ROOT_DIR")}"

if [ -z "${1:-}" ]; then
  echo "Usage: ./scripts/restore-kafka-volume.sh <timestamp>"
  echo "Example: ./scripts/restore-kafka-volume.sh 20260310_120000"
  exit 1
fi

TIMESTAMP="$1"
SERVICES=("kafka-1:kafka_1_data" "kafka-2:kafka_2_data" "kafka-3:kafka_3_data")

resolve_volume_name() {
  local service="$1"
  local logical_volume="$2"

  if docker compose -f "$ROOT_DIR/docker-compose.yaml" ps -q "$service" >/dev/null 2>&1; then
    local container_id
    container_id="$(docker compose -f "$ROOT_DIR/docker-compose.yaml" ps -q "$service" | head -n 1)"
    if [ -n "$container_id" ]; then
      local mounted_volume
      mounted_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/kafka/data"}}{{.Name}}{{end}}{{end}}' "$container_id")"
      if [ -n "$mounted_volume" ]; then
        echo "$mounted_volume"
        return 0
      fi
    fi
  fi

  local fallback_volume
  fallback_volume="$(docker volume ls --filter "label=com.docker.compose.project=$PROJECT_NAME" --filter "label=com.docker.compose.volume=$logical_volume" --format '{{.Name}}' | head -n 1)"
  if [ -n "$fallback_volume" ]; then
    echo "$fallback_volume"
    return 0
  fi

  echo ""
}

echo "Восстанавливаю Kafka cluster backup по timestamp=$TIMESTAMP"
echo "Перед восстановлением рекомендуется остановить стек: docker compose down"

for entry in "${SERVICES[@]}"; do
  service="${entry%%:*}"
  logical_volume="${entry##*:}"

  volume_name="$(resolve_volume_name "$service" "$logical_volume")"
  if [ -z "$volume_name" ]; then
    echo "Не удалось определить volume для $service ($logical_volume)"
    exit 1
  fi

  archive_name="${service}_${TIMESTAMP}.tar.gz"
  archive_path="$BACKUP_DIR/$archive_name"

  if [ ! -f "$archive_path" ]; then
    echo "Архив не найден: $archive_path"
    exit 1
  fi

  echo "[$service] $archive_path -> $volume_name"

  if [ "$CLEAR_VOLUME_BEFORE_RESTORE" = "true" ]; then
    docker run --rm \
      -v "$volume_name:/volume" \
      alpine:3.20 \
      sh -ec 'rm -rf /volume/* /volume/.[!.]* /volume/..?* || true'
  fi

  docker run --rm \
    -v "$volume_name:/volume" \
    -v "$BACKUP_DIR:/backup" \
    alpine:3.20 \
    sh -ec "tar -C /volume -xzf /backup/$archive_name"
done

echo "Restore завершен."
