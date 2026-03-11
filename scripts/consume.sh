#!/bin/bash
set -euo pipefail

BROKER="${BROKER:-kafka-1:9092,kafka-2:9092,kafka-3:9092}"
TOPIC="${TOPIC:-transactions}"
GROUP_ID="${GROUP_ID:-transactions-workers}"
WORKER_ID="${1:-worker-unknown}"

echo "[$WORKER_ID] Starting consumer for topic '$TOPIC' in group '$GROUP_ID'"

/opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server "$BROKER" \
  --topic "$TOPIC" \
  --group "$GROUP_ID" \
  --property print.timestamp=true \
  --property print.partition=true \
  --property print.offset=true |
while IFS= read -r line; do
  printf '[%s] %s\n' "$WORKER_ID" "$line"
done
