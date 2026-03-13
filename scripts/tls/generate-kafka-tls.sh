#!/bin/bash

# Строгий режим:
# -e: завершать скрипт при любой ошибке команды
# -u: считать ошибкой обращение к неинициализированной переменной
# -o pipefail: считать ошибкой падение любой команды в пайплайне
set -euo pipefail

# Абсолютный путь до каталога, где лежит сам скрипт.
# Это позволяет запускать скрипт из любой директории.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Каталог, куда складываем итоговые TLS-артефакты.
# По умолчанию: папка скрипта (`scripts/tls`).
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR}"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PROJECT_ENV_FILE="${PROJECT_ENV_FILE:-$PROJECT_ROOT/.env}"

# PUBLIC_HOST обязателен и должен быть IPv4.
# Этот IP добавляется в SAN сертификата брокера как IP.2.
PUBLIC_HOST="${PUBLIC_HOST:-}"

# Пароли для keystore/truststore Kafka.
# Берутся из env, чтобы совпадать с docker-compose/.env.
STORE_PASSWORD="${KAFKA_SSL_KEYSTORE_PASSWORD:-changeit}"
KEY_PASSWORD="${KAFKA_SSL_KEY_PASSWORD:-$STORE_PASSWORD}"
TRUSTSTORE_PASSWORD="${KAFKA_SSL_TRUSTSTORE_PASSWORD:-changeit}"

# Гарантируем существование целевого каталога.
mkdir -p "$OUT_DIR"

# Временная директория для промежуточных файлов (CSR, p12, openssl.cnf).
TMP_DIR="$(mktemp -d)"

# Всегда очищаем временную папку при выходе (успех/ошибка/interrupt).
trap 'rm -rf "$TMP_DIR"' EXIT

# PUBLIC_HOST обязателен.
# Если переменная не передана явно, пробуем взять её из .env проекта.
if [[ -z "$PUBLIC_HOST" ]]; then
  if [[ -f "$PROJECT_ENV_FILE" ]]; then
    PUBLIC_HOST="$(
      awk -F= '/^[[:space:]]*PUBLIC_HOST=/{print $2}' "$PROJECT_ENV_FILE" \
        | tail -n1 \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
    )"
  fi
fi

if [[ -z "$PUBLIC_HOST" ]]; then
  echo "[tls] error: PUBLIC_HOST is required and must be IPv4 address" >&2
  echo "[tls] tried env file: $PROJECT_ENV_FILE" >&2
  echo "[tls] example: PUBLIC_HOST=10.20.30.40 ./scripts/tls/generate-kafka-tls.sh" >&2
  exit 1
fi

# Разрешаем только IPv4 в формате a.b.c.d (без DNS/FQDN).
if ! [[ "$PUBLIC_HOST" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "[tls] error: PUBLIC_HOST must be IPv4 address (DNS name is not supported in this mode)" >&2
  echo "[tls] got: $PUBLIC_HOST" >&2
  exit 1
fi

# Дополнительная проверка диапазона октетов (0..255).
IFS='.' read -r o1 o2 o3 o4 <<<"$PUBLIC_HOST"
for octet in "$o1" "$o2" "$o3" "$o4"; do
  if (( octet < 0 || octet > 255 )); then
    echo "[tls] error: invalid IPv4 octet in PUBLIC_HOST: $PUBLIC_HOST" >&2
    exit 1
  fi
done

# Генерируем openssl-конфиг.
# SAN содержит:
# - 127.0.0.1 (для локального теста на той же машине)
# - PUBLIC_HOST (основной адрес подключения клиентов)
cat >"$TMP_DIR/openssl.cnf" <<EOF
[ req ]
default_bits       = 2048
prompt             = no
default_md         = sha256
req_extensions     = req_ext
distinguished_name = dn

[ dn ]
CN = ${PUBLIC_HOST}

[ req_ext ]
subjectAltName = @alt_names

[ alt_names ]
IP.1 = 127.0.0.1
IP.2 = ${PUBLIC_HOST}
EOF

echo "[tls] generating CA and broker certificate for PUBLIC_HOST=${PUBLIC_HOST} (IP-only SAN)"

# Шаг 1. Генерация CA и broker-сертификата через openssl в отдельном контейнере.
# Выходные файлы во временной директории /work:
# - ca.key / ca.crt
# - broker.key / broker.csr / broker.crt / broker.p12
docker run --rm -v "$TMP_DIR:/work" alpine:3.20 sh -ec "
  apk add --no-cache openssl >/dev/null
  openssl genrsa -out /work/ca.key 4096
  openssl req -x509 -new -nodes -key /work/ca.key -sha256 -days 3650 -subj '/CN=Kafka-Local-CA' -out /work/ca.crt
  openssl genrsa -out /work/broker.key 2048
  openssl req -new -key /work/broker.key -out /work/broker.csr -config /work/openssl.cnf
  openssl x509 -req -in /work/broker.csr -CA /work/ca.crt -CAkey /work/ca.key -CAcreateserial -out /work/broker.crt -days 3650 -sha256 -extfile /work/openssl.cnf -extensions req_ext
  openssl pkcs12 -export -in /work/broker.crt -inkey /work/broker.key -name kafka-broker -out /work/broker.p12 -passout pass:${STORE_PASSWORD}
"

# Шаг 2. Преобразование p12 -> JKS + создание truststore через keytool.
# Делаем в отдельном Java-контейнере, чтобы не требовать локальный keytool.
docker run --rm -v "$TMP_DIR:/work" eclipse-temurin:21-jre sh -ec "
  keytool -importkeystore \
    -srckeystore /work/broker.p12 \
    -srcstoretype PKCS12 \
    -srcstorepass ${STORE_PASSWORD} \
    -destkeystore /work/broker.keystore.jks \
    -deststoretype JKS \
    -deststorepass ${STORE_PASSWORD} \
    -destkeypass ${KEY_PASSWORD} \
    -alias kafka-broker \
    -noprompt

  keytool -import \
    -alias kafka-ca \
    -file /work/ca.crt \
    -keystore /work/broker.truststore.jks \
    -storepass ${TRUSTSTORE_PASSWORD} \
    -noprompt
"

# Копируем итоговые артефакты в OUT_DIR:
# - ca.crt: для внешних клиентов (python/kcat/другие)
# - broker.keystore.jks: для Kafka broker
# - broker.truststore.jks: для Kafka broker
cp "$TMP_DIR/ca.crt" "$OUT_DIR/ca.crt"
cp "$TMP_DIR/broker.keystore.jks" "$OUT_DIR/broker.keystore.jks"
cp "$TMP_DIR/broker.truststore.jks" "$OUT_DIR/broker.truststore.jks"

echo "[tls] generated:"
echo "  $OUT_DIR/ca.crt"
echo "  $OUT_DIR/broker.keystore.jks"
echo "  $OUT_DIR/broker.truststore.jks"
echo
echo "[tls] next step:"
# После регенерации сертификатов брокеры нужно пересоздать,
# чтобы они перечитали keystore/truststore из volume.
echo "  recreate kafka brokers to apply TLS on EXTERNAL listener"
