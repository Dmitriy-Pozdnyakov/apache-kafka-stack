#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_FILE="${OUT_FILE:-$SCRIPT_DIR/vmui_htpasswd}"
VMUI_USER="${VMUI_USER:-vmui}"
VMUI_PASSWORD="${VMUI_PASSWORD:-}"

if [ -z "$VMUI_PASSWORD" ]; then
  echo "Usage: VMUI_PASSWORD='<password>' ./scripts/nginx/generate-vmui-htpasswd.sh"
  exit 1
fi

HASH="$(openssl passwd -apr1 "$VMUI_PASSWORD")"
printf '%s:%s\n' "$VMUI_USER" "$HASH" > "$OUT_FILE"

chmod 600 "$OUT_FILE"
echo "[vmui] htpasswd generated: $OUT_FILE"
