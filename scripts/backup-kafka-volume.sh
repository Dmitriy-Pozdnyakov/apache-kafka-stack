#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$ROOT_DIR")}"

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

mkdir -p "$BACKUP_DIR"

echo "Создаю backup для всех брокеров кластера (timestamp=$TIMESTAMP)"
echo "Рекомендуется выполнять backup при остановленном стеке для консистентности."

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

  echo "[$service] $volume_name -> $archive_path"
  docker run --rm \
    -v "$volume_name:/volume:ro" \
    -v "$BACKUP_DIR:/backup" \
    alpine:3.20 \
    sh -ec "tar -C /volume -czf /backup/$archive_name ."
done

echo "Backup завершен. Архивы в: $BACKUP_DIR"
echo "Для restore используй timestamp: $TIMESTAMP"
