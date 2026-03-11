#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR}"
PUBLIC_HOST="${PUBLIC_HOST:-localhost}"
STORE_PASSWORD="${KAFKA_SSL_KEYSTORE_PASSWORD:-changeit}"
KEY_PASSWORD="${KAFKA_SSL_KEY_PASSWORD:-$STORE_PASSWORD}"
TRUSTSTORE_PASSWORD="${KAFKA_SSL_TRUSTSTORE_PASSWORD:-changeit}"

mkdir -p "$OUT_DIR"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

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
DNS.1 = ${PUBLIC_HOST}
DNS.2 = localhost
DNS.3 = kafka-1
DNS.4 = kafka-2
DNS.5 = kafka-3
IP.1 = 127.0.0.1
EOF

echo "[tls] generating CA and broker certificate for host=${PUBLIC_HOST}"

docker run --rm -v "$TMP_DIR:/work" alpine:3.20 sh -ec "
  apk add --no-cache openssl >/dev/null
  openssl genrsa -out /work/ca.key 4096
  openssl req -x509 -new -nodes -key /work/ca.key -sha256 -days 3650 -subj '/CN=Kafka-Local-CA' -out /work/ca.crt
  openssl genrsa -out /work/broker.key 2048
  openssl req -new -key /work/broker.key -out /work/broker.csr -config /work/openssl.cnf
  openssl x509 -req -in /work/broker.csr -CA /work/ca.crt -CAkey /work/ca.key -CAcreateserial -out /work/broker.crt -days 3650 -sha256 -extfile /work/openssl.cnf -extensions req_ext
  openssl pkcs12 -export -in /work/broker.crt -inkey /work/broker.key -name kafka-broker -out /work/broker.p12 -passout pass:${STORE_PASSWORD}
"

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

cp "$TMP_DIR/ca.crt" "$OUT_DIR/ca.crt"
cp "$TMP_DIR/broker.keystore.jks" "$OUT_DIR/broker.keystore.jks"
cp "$TMP_DIR/broker.truststore.jks" "$OUT_DIR/broker.truststore.jks"

echo "[tls] generated:"
echo "  $OUT_DIR/ca.crt"
echo "  $OUT_DIR/broker.keystore.jks"
echo "  $OUT_DIR/broker.truststore.jks"
echo
echo "[tls] next step:"
echo "  recreate kafka brokers to apply TLS on EXTERNAL listener"
