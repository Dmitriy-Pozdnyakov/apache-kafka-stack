#!/bin/bash
set -euo pipefail

BROKER="${BROKER:-kafka-1:9092}"
TOPIC="${TOPIC:-transactions}"
RETRY_TOPIC="${RETRY_TOPIC:-transactions.retry}"
DLQ_TOPIC="${DLQ_TOPIC:-transactions.dlq}"
TOPIC_PARTITIONS="${TOPIC_PARTITIONS:-3}"
TOPIC_REPLICATION_FACTOR="${TOPIC_REPLICATION_FACTOR:-3}"
KAFKA_INIT_WAIT_TIMEOUT_SEC="${KAFKA_INIT_WAIT_TIMEOUT_SEC:-240}"
TOPIC_RETENTION_MS="${TOPIC_RETENTION_MS:-604800000}"
TOPIC_CLEANUP_POLICY="${TOPIC_CLEANUP_POLICY:-delete}"
RETRY_TOPIC_RETENTION_MS="${RETRY_TOPIC_RETENTION_MS:-86400000}"
RETRY_TOPIC_CLEANUP_POLICY="${RETRY_TOPIC_CLEANUP_POLICY:-delete}"
DLQ_TOPIC_RETENTION_MS="${DLQ_TOPIC_RETENTION_MS:-1209600000}"
DLQ_TOPIC_CLEANUP_POLICY="${DLQ_TOPIC_CLEANUP_POLICY:-delete}"
KAFKA_CONFIG_APPLY_TIMEOUT_SEC="${KAFKA_CONFIG_APPLY_TIMEOUT_SEC:-30}"

run_with_timeout() {
  local description="$1"
  shift
  if ! timeout "${KAFKA_CONFIG_APPLY_TIMEOUT_SEC}"s "$@"; then
    echo "[kafka-init] WARN: ${description} failed or timed out after ${KAFKA_CONFIG_APPLY_TIMEOUT_SEC}s"
  fi
}

echo "[kafka-init] Waiting for Kafka on ${BROKER}..."

START_TS="$(date +%s)"
until /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BROKER" --list >/dev/null 2>&1; do
  NOW_TS="$(date +%s)"
  ELAPSED="$((NOW_TS - START_TS))"
  if [ "$ELAPSED" -ge "$KAFKA_INIT_WAIT_TIMEOUT_SEC" ]; then
    echo "[kafka-init] ERROR: Kafka is not reachable after ${KAFKA_INIT_WAIT_TIMEOUT_SEC}s"
    exit 1
  fi
  sleep 2
done

echo "[kafka-init] Kafka is reachable. Creating/updating topics..."

run_with_timeout "create TOPIC" \
  /opt/kafka/bin/kafka-topics.sh \
  --create \
  --if-not-exists \
  --topic "$TOPIC" \
  --bootstrap-server "$BROKER" \
  --replication-factor "$TOPIC_REPLICATION_FACTOR" \
  --partitions "$TOPIC_PARTITIONS" \
  --config "retention.ms=${TOPIC_RETENTION_MS}" \
  --config "cleanup.policy=${TOPIC_CLEANUP_POLICY}"

run_with_timeout "alter TOPIC partitions" \
  /opt/kafka/bin/kafka-topics.sh \
  --alter \
  --topic "$TOPIC" \
  --bootstrap-server "$BROKER" \
  --partitions "$TOPIC_PARTITIONS"

run_with_timeout "create RETRY_TOPIC" \
  /opt/kafka/bin/kafka-topics.sh \
  --create \
  --if-not-exists \
  --topic "$RETRY_TOPIC" \
  --bootstrap-server "$BROKER" \
  --replication-factor "$TOPIC_REPLICATION_FACTOR" \
  --partitions "$TOPIC_PARTITIONS" \
  --config "retention.ms=${RETRY_TOPIC_RETENTION_MS}" \
  --config "cleanup.policy=${RETRY_TOPIC_CLEANUP_POLICY}"

run_with_timeout "create DLQ_TOPIC" \
  /opt/kafka/bin/kafka-topics.sh \
  --create \
  --if-not-exists \
  --topic "$DLQ_TOPIC" \
  --bootstrap-server "$BROKER" \
  --replication-factor "$TOPIC_REPLICATION_FACTOR" \
  --partitions "$TOPIC_PARTITIONS" \
  --config "retention.ms=${DLQ_TOPIC_RETENTION_MS}" \
  --config "cleanup.policy=${DLQ_TOPIC_CLEANUP_POLICY}"

run_with_timeout "apply TOPIC configs" \
  /opt/kafka/bin/kafka-configs.sh \
  --alter \
  --bootstrap-server "$BROKER" \
  --entity-type topics \
  --entity-name "$TOPIC" \
  --add-config "retention.ms=${TOPIC_RETENTION_MS},cleanup.policy=${TOPIC_CLEANUP_POLICY}"

run_with_timeout "apply RETRY_TOPIC configs" \
  /opt/kafka/bin/kafka-configs.sh \
  --alter \
  --bootstrap-server "$BROKER" \
  --entity-type topics \
  --entity-name "$RETRY_TOPIC" \
  --add-config "retention.ms=${RETRY_TOPIC_RETENTION_MS},cleanup.policy=${RETRY_TOPIC_CLEANUP_POLICY}"

run_with_timeout "apply DLQ_TOPIC configs" \
  /opt/kafka/bin/kafka-configs.sh \
  --alter \
  --bootstrap-server "$BROKER" \
  --entity-type topics \
  --entity-name "$DLQ_TOPIC" \
  --add-config "retention.ms=${DLQ_TOPIC_RETENTION_MS},cleanup.policy=${DLQ_TOPIC_CLEANUP_POLICY}"

echo "[kafka-init] Topic descriptions:"
run_with_timeout "describe TOPIC" /opt/kafka/bin/kafka-topics.sh --describe --topic "$TOPIC" --bootstrap-server "$BROKER"
run_with_timeout "describe RETRY_TOPIC" /opt/kafka/bin/kafka-topics.sh --describe --topic "$RETRY_TOPIC" --bootstrap-server "$BROKER"
run_with_timeout "describe DLQ_TOPIC" /opt/kafka/bin/kafka-topics.sh --describe --topic "$DLQ_TOPIC" --bootstrap-server "$BROKER"

echo "[kafka-init] Done."
