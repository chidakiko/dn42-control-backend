#!/usr/bin/env bash
set -euo pipefail

wait_for_interface() {
    local interface_name="$1"
    local attempts="${2:-30}"

    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if ip link show "${interface_name}" >/dev/null 2>&1; then
            return 0
        fi
        echo "[bird] waiting for ${interface_name} (${attempt}/${attempts})"
        sleep 1
    done

    echo "[bird] interface ${interface_name} did not appear" >&2
    return 1
}

EXPECTED_INTERFACES="${DN42_BIRD_WAIT_INTERFACES:-as4242420001 dn42-lo dns-anycast igp-edge2}"
for interface_name in ${EXPECTED_INTERFACES}; do
    wait_for_interface "${interface_name}"
done

/opt/dn42/scripts/bird/apply-bird.sh
echo "[bird] router ready"
exec sleep infinity
