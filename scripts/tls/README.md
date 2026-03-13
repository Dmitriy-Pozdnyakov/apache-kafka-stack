# TLS Mini Guide

Мини-инструкция по генерации сертификатов Kafka (режим IP-only, без DNS/FQDN).

## Что делает скрипт

`generate-kafka-tls.sh` генерирует:

- `ca.crt` — корневой сертификат CA для клиентов
- `broker.keystore.jks` — keystore брокера Kafka
- `broker.truststore.jks` — truststore брокера Kafka

## Предусловия

- Docker должен быть установлен и запущен
- В `.env` должен быть корректный IPv4:

```env
PUBLIC_HOST=10.20.30.40
```

## Запуск

Из корня `apache-kafka-stack` (рекомендуется):

```bash
./scripts/tls/generate-kafka-tls.sh
```

Скрипт автоматически возьмёт `PUBLIC_HOST` из `.env`.

Явный override (если нужно временно использовать другой IP):

```bash
PUBLIC_HOST=10.20.30.40 ./scripts/tls/generate-kafka-tls.sh
```

Примечание: не используй `source .env` для этого шага, потому что в `.env` есть значения с пробелами
(например `KAFKA_HEAP_OPTS`), и shell может выдать `command not found`.

## Применение к Kafka

После генерации нужно пересоздать брокеры:

```bash
docker compose up -d --force-recreate kafka-1 kafka-2 kafka-3
```

## Использование в Python-клиентах

Указывай CA-файл:

```bash
--ssl-cafile ./scripts/tls/ca.crt
```

Важно: подключайся к Kafka по тому же IP, который записан в `PUBLIC_HOST`.
