#!/usr/bin/env bash
set -euo pipefail

BIRD_CONF="${BIRD_CONF:-/etc/bird/bird.conf}"

echo "[bird] preparing runtime directory"
mkdir -p /run/bird
mkdir -p /var/log/bird

echo "[bird] checking config ${BIRD_CONF}"
bird -p -c "${BIRD_CONF}"

if birdc show status >/dev/null 2>&1; then
    echo "[bird] reconfiguring existing daemon"
    birdc configure
else
    echo "[bird] starting daemon"
    bird -c "${BIRD_CONF}"
fi

echo "[done] BIRD applied"
birdc show status
