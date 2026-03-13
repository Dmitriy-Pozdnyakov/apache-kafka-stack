#!/usr/bin/env python3
import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Set

from kafka import KafkaConsumer, KafkaProducer

DEFAULT_BROKER = "127.0.0.1:19092,127.0.0.1:19093,127.0.0.1:19094"
DEFAULT_TOPIC = "transactions"
DEFAULT_SECURITY_PROTOCOL = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
DEFAULT_SSL_CAFILE = os.getenv("SSL_CAFILE", "")
DEFAULT_SSL_CHECK_HOSTNAME = os.getenv("SSL_CHECK_HOSTNAME", "true").lower() == "true"
DEFAULT_SASL_MECHANISM = os.getenv("KAFKA_SASL_MECHANISM", "PLAIN")
DEFAULT_SASL_USERNAME = os.getenv("KAFKA_SASL_USERNAME", "")
DEFAULT_SASL_PASSWORD = os.getenv("KAFKA_SASL_PASSWORD", "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kafka load test: produce N messages and verify consumed count"
    )
    parser.add_argument("--broker", default=os.getenv("BROKER", DEFAULT_BROKER))
    parser.add_argument("--topic", default=os.getenv("TOPIC", DEFAULT_TOPIC))
    parser.add_argument("--group-id", default="load-test-group")
    parser.add_argument("--messages", type=int, default=5000)
    parser.add_argument("--partitions", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--linger-ms", type=int, default=10)
    parser.add_argument("--timeout-sec", type=int, default=120)
    parser.add_argument("--security-protocol", default=DEFAULT_SECURITY_PROTOCOL)
    parser.add_argument("--ssl-cafile", default=DEFAULT_SSL_CAFILE)
    parser.add_argument(
        "--ssl-check-hostname",
        type=lambda x: str(x).lower() == "true",
        default=DEFAULT_SSL_CHECK_HOSTNAME,
    )
    parser.add_argument("--sasl-mechanism", default=DEFAULT_SASL_MECHANISM)
    parser.add_argument("--sasl-username", default=DEFAULT_SASL_USERNAME)
    parser.add_argument("--sasl-password", default=DEFAULT_SASL_PASSWORD)
    return parser


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_load(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def build_kafka_conn_params(args: argparse.Namespace, brokers: list) -> dict:
    params = {"bootstrap_servers": brokers}
    if args.security_protocol.upper() == "SSL":
        params["security_protocol"] = "SSL"
        if args.ssl_cafile:
            params["ssl_cafile"] = args.ssl_cafile
        params["ssl_check_hostname"] = args.ssl_check_hostname
    if args.security_protocol.upper() in {"SASL_SSL", "SASL_PLAINTEXT"}:
        params["security_protocol"] = args.security_protocol.upper()
        params["sasl_mechanism"] = args.sasl_mechanism
        params["sasl_plain_username"] = args.sasl_username
        params["sasl_plain_password"] = args.sasl_password
        if args.security_protocol.upper() == "SASL_SSL":
            if args.ssl_cafile:
                params["ssl_cafile"] = args.ssl_cafile
            params["ssl_check_hostname"] = args.ssl_check_hostname
    return params


def main() -> int:
    args = build_parser().parse_args()
    run_id = f"run-{int(time.time())}"
    brokers = [b.strip() for b in args.broker.split(",") if b.strip()]

    expected_ids: Set[int] = set(range(args.messages))
    consumed_ids: Set[int] = set()
    duplicates = 0
    parse_errors = 0

    stop_event = threading.Event()

    consumer = KafkaConsumer(
        args.topic,
        **build_kafka_conn_params(args, brokers),
        group_id=f"{args.group_id}-{run_id}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
    )

    def consume_worker() -> None:
        nonlocal duplicates, parse_errors
        while not stop_event.is_set():
            records = consumer.poll(timeout_ms=1000)
            if not records:
                continue
            for messages in records.values():
                for msg in messages:
                    payload = safe_json_load(msg.value)
                    if not isinstance(payload, dict):
                        parse_errors += 1
                        continue
                    if payload.get("run_id") != run_id:
                        continue
                    seq = payload.get("seq")
                    if not isinstance(seq, int):
                        parse_errors += 1
                        continue
                    if seq in consumed_ids:
                        duplicates += 1
                    consumed_ids.add(seq)
                    if len(consumed_ids) >= args.messages:
                        stop_event.set()
                        return

    consumer_thread = threading.Thread(target=consume_worker, daemon=True)
    consumer_thread.start()

    producer = KafkaProducer(
        **build_kafka_conn_params(args, brokers),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
        linger_ms=args.linger_ms,
        batch_size=args.batch_size,
    )

    print(f"[load-test] start run_id={run_id} brokers={brokers} topic={args.topic}")
    print(
        f"[load-test] produce messages={args.messages} batch_size={args.batch_size} linger_ms={args.linger_ms}"
    )

    produced = 0
    produce_started = time.time()
    for i in range(args.messages):
        payload: Dict[str, object] = {
            "run_id": run_id,
            "seq": i,
            "created_at": now_utc(),
            "payload": f"load-test-message-{i}",
        }
        producer.send(args.topic, value=payload)
        produced += 1

    producer.flush()
    produce_elapsed = time.time() - produce_started

    wait_deadline = time.time() + args.timeout_sec
    while time.time() < wait_deadline and len(consumed_ids) < args.messages:
        time.sleep(0.5)

    stop_event.set()
    consumer_thread.join(timeout=5)
    consumer.close()
    producer.close()

    received = len(consumed_ids)
    lost = len(expected_ids - consumed_ids)

    summary = {
        "run_id": run_id,
        "brokers": brokers,
        "topic": args.topic,
        "messages_produced": produced,
        "messages_consumed": received,
        "duplicates": duplicates,
        "parse_errors": parse_errors,
        "lost": lost,
        "produce_elapsed_sec": round(produce_elapsed, 3),
        "producer_msg_per_sec": round(produced / produce_elapsed, 2)
        if produce_elapsed > 0
        else 0.0,
        "status": "OK" if produced == received and lost == 0 else "MISMATCH",
    }

    print("[load-test] summary:")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    print("[load-test] UI check:")
    print("  1) Kafka UI -> Topics ->", args.topic)
    print("  2) Producers/consumers throughput во время теста")
    print("  3) Consumer groups ->", f"{args.group_id}-{run_id}")
    print("  4) Сверить messages_produced/messages_consumed из summary")

    return 0 if summary["status"] == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
