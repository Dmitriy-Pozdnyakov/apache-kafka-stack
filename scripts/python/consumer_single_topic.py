#!/usr/bin/env python3
import json
import os

from kafka import KafkaConsumer

BROKER = os.getenv("BROKER", "127.0.0.1:19092,127.0.0.1:19093,127.0.0.1:19094")
TOPIC = os.getenv("TOPIC", "transactions")
GROUP_ID = os.getenv("GROUP_ID", "transactions-workers")
AUTO_OFFSET_RESET = os.getenv("AUTO_OFFSET_RESET", "earliest")
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
    consumer = KafkaConsumer(
        TOPIC,
        **build_kafka_conn_params(),
        group_id=GROUP_ID,
        auto_offset_reset=AUTO_OFFSET_RESET,
        enable_auto_commit=True,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )

    print(
        f"Listening topic={TOPIC} group_id={GROUP_ID} broker={BROKER} "
        f"auto_offset_reset={AUTO_OFFSET_RESET}"
    )

    try:
        for msg in consumer:
            print(
                "topic={topic} partition={partition} offset={offset} key={key} value={value}".format(
                    topic=msg.topic,
                    partition=msg.partition,
                    offset=msg.offset,
                    key=msg.key.decode("utf-8") if msg.key else None,
                    value=msg.value,
                )
            )
    except KeyboardInterrupt:
        print("Stopping consumer...")
    finally:
        consumer.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
