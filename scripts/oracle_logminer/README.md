# Oracle LogMiner -> Kafka

Скрипты для Oracle LogMiner:

- `logminer_poll.py` — пишет изменения в CSV
- `oracle_to_kafka_producer.py` — пишет изменения в Kafka topic

## Установка

```bash
python3 -m pip install -r scripts/oracle_logminer/requirements.txt
```

## Обязательные env

- `ORACLE_USER`
- `ORACLE_PASSWORD`
- `ORACLE_DSN`
- `KAFKA_BROKER` (или `BROKER`)

## Полезные env

- `KAFKA_TOPIC=oracle.logminer.raw`
- `POLL_SECONDS=60`
- `STATE_FILE=./oracle_kafka_state.json`
- `START_FROM_SCN=<число>`
- `KAFKA_SECURITY_PROTOCOL=SASL_SSL|SASL_PLAINTEXT|SSL|PLAINTEXT`
- `SSL_CAFILE=./scripts/tls/ca.crt`
- `KAFKA_SASL_MECHANISM=PLAIN`
- `KAFKA_SASL_USERNAME=...`
- `KAFKA_SASL_PASSWORD=...`

## Пример запуска (TLS + SASL)

```bash
ORACLE_USER=system \
ORACLE_PASSWORD=oracle_password \
ORACLE_DSN='10.20.30.50:1521/XEPDB1' \
KAFKA_BROKER='10.20.30.40:19092,10.20.30.40:19093,10.20.30.40:19094' \
KAFKA_TOPIC='oracle.logminer.raw' \
KAFKA_SECURITY_PROTOCOL='SASL_SSL' \
SSL_CAFILE='./scripts/tls/ca.crt' \
KAFKA_SASL_MECHANISM='PLAIN' \
KAFKA_SASL_USERNAME='kafka_user' \
KAFKA_SASL_PASSWORD='change_me_kafka_password' \
python3 scripts/oracle_logminer/oracle_to_kafka_producer.py
```

## Как стыковать с sink в Postgres

1. Producer (этот скрипт) публикует события в `KAFKA_TOPIC`.
2. Consumer читает этот topic своей `group_id`.
3. После успешного upsert в Postgres делает commit offset.

Рекомендуемый режим:

- producer: `oneshot + внешний scheduler` или polling (`POLL_SECONDS`)
- consumer: `continuous`
