#!/usr/bin/env bash
set -euo pipefail

IF="dn42-lo"
echo "[dummy] applying ${IF}"
ip link show "${IF}" >/dev/null 2>&1 || ip link add dev "${IF}" type dummy
ip link set "${IF}" up
ip addr replace '172.20.0.62/32' dev "${IF}"
ip -6 addr replace 'fdce:1111:2222:9500::1/128' dev "${IF}"
echo "[dummy] ${IF} applied"
ip addr show dev "${IF}"
