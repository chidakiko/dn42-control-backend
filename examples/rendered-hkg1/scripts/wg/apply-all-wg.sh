#!/usr/bin/env bash
set -euo pipefail

/opt/dn42/scripts/wg/apply-dn42-lo.sh
/opt/dn42/scripts/wg/apply-as4242420001.sh
/opt/dn42/scripts/wg/apply-igp-edge2.sh

echo "[done] WireGuard links applied"
wg show
