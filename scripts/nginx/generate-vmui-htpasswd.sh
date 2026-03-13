#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PROJECT_ENV_FILE="${PROJECT_ENV_FILE:-$PROJECT_ROOT/.env}"
OUT_FILE="${OUT_FILE:-$SCRIPT_DIR/vmui_htpasswd}"
VMUI_USER="${VMUI_USER:-}"
VMUI_PASSWORD="${VMUI_PASSWORD:-}"

if [[ -f "$PROJECT_ENV_FILE" ]]; then
  if [[ -z "$VMUI_USER" ]]; then
    VMUI_USER="$(
      awk -F= '/^[[:space:]]*VMUI_USER=/{print $2}' "$PROJECT_ENV_FILE" \
        | tail -n1 \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
    )"
  fi
  if [[ -z "$VMUI_PASSWORD" ]]; then
    VMUI_PASSWORD="$(
      awk -F= '/^[[:space:]]*VMUI_PASSWORD=/{print $2}' "$PROJECT_ENV_FILE" \
        | tail -n1 \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
    )"
  fi
fi

VMUI_USER="${VMUI_USER:-vmui}"

if [ -z "$VMUI_PASSWORD" ]; then
  echo "Usage: VMUI_PASSWORD='<password>' ./scripts/nginx/generate-vmui-htpasswd.sh"
  echo "or set VMUI_USER/VMUI_PASSWORD in $PROJECT_ENV_FILE and run script without args"
  exit 1
fi

HASH="$(openssl passwd -apr1 "$VMUI_PASSWORD")"
printf '%s:%s\n' "$VMUI_USER" "$HASH" > "$OUT_FILE"

chmod 600 "$OUT_FILE"
echo "[vmui] htpasswd generated: $OUT_FILE"
