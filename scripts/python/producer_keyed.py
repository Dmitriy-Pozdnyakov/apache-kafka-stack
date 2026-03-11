#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime, timezone

from kafka import KafkaProducer

BROKER = os.getenv("BROKER", "localhost:19092,localhost:19093,localhost:19094")
TOPIC = os.getenv("TOPIC", "transactions")
KAFKA_SECURITY_PROTOCOL = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
SSL_CAFILE = os.getenv("SSL_CAFILE", "")
SSL_CHECK_HOSTNAME = os.getenv("SSL_CHECK_HOSTNAME", "true").lower() == "true"
KAFKA_SASL_MECHANISM = os.getenv("KAFKA_SASL_MECHANISM", "PLAIN")
KAFKA_SASL_USERNAME = os.getenv("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASSWORD = os.getenv("KAFKA_SASL_PASSWORD", "")


def build_kafka_conn_params() -> dict:
    params = {"bootstrap_servers": [BROKER]}
    if KAFKA_SECURITY_PROTOCOL.upper() == "SSL":
        params["security_protocol"] = "SSL"
        if SSL_CAFILE:
            params["ssl_cafile"] = SSL_CAFILE
        params["ssl_check_hostname"] = SSL_CHECK_HOSTNAME
    if KAFKA_SECURITY_PROTOCOL.upper() in {"SASL_SSL", "SASL_PLAINTEXT"}:
        params["security_protocol"] = KAFKA_SECURITY_PROTOCOL.upper()
        params["sasl_mechanism"] = KAFKA_SASL_MECHANISM
        params["sasl_plain_username"] = KAFKA_SASL_USERNAME
        params["sasl_plain_password"] = KAFKA_SASL_PASSWORD
        if KAFKA_SECURITY_PROTOCOL.upper() == "SASL_SSL":
            if SSL_CAFILE:
                params["ssl_cafile"] = SSL_CAFILE
            params["ssl_check_hostname"] = SSL_CHECK_HOSTNAME
    return params


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python scripts/python/producer_keyed.py '<key>' '<message>'")
        return 1

    key = sys.argv[1]
    message = sys.argv[2]
    payload = {
        "event": message,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    producer = KafkaProducer(
        **build_kafka_conn_params(),
        key_serializer=lambda k: k.encode("utf-8"),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
    )

    future = producer.send(TOPIC, key=key, value=payload)
    record = future.get(timeout=10)
    print(
        "Sent "
        f"key={key} topic={record.topic} partition={record.partition} offset={record.offset}"
    )

    producer.flush()
    producer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
