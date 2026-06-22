#!/usr/bin/env bash
set -euo pipefail

IF='as4242420001'
WG_CONF="/etc/wireguard/${IF}.conf"
PRIVATE_DIR="/run/dn42-control/wireguard"
PRIVATE_CONF="${PRIVATE_DIR}/${IF}.conf"
SECRET_KEY_FILE="/run/dn42-control/secrets/node.key"
TMP_CONF="$(mktemp)"
trap 'rm -f "${TMP_CONF}"' EXIT

echo "[wg] applying ${IF}"
mkdir -p "${PRIVATE_DIR}"
cp "${WG_CONF}" "${PRIVATE_CONF}"
chmod 600 "${PRIVATE_CONF}"
# 持久 .conf 里的 PrivateKey 是 secret:// 占位符；若 agent 已把本地私钥推进
# 临时密钥文件，则只在这份临时副本里替换为真实私钥（base64 无反斜杠，awk -v 安全），
# 喂给 wg syncconf。持久产物、config_hash、上报、日志全程不见明文。
if [ -f "${SECRET_KEY_FILE}" ]; then
  WG_KEY="$(cat "${SECRET_KEY_FILE}")"
  awk -v k="${WG_KEY}" '/^[[:space:]]*PrivateKey[[:space:]]*=/{print "PrivateKey = " k; next} {print}' "${PRIVATE_CONF}" > "${PRIVATE_CONF}.resolved"
  mv "${PRIVATE_CONF}.resolved" "${PRIVATE_CONF}"
fi
ip link show "${IF}" >/dev/null 2>&1 || ip link add dev "${IF}" type wireguard
wg-quick strip "${PRIVATE_CONF}" > "${TMP_CONF}"
wg syncconf "${IF}" "${TMP_CONF}"
ip addr replace '172.20.0.62/32' peer '172.20.0.105/32' dev "${IF}"
ip -6 addr replace 'fdce:1111:2222:9500::1/128' peer 'fdce:1111:2222:dead::11/128' dev "${IF}"
ip link set "${IF}" mtu 1420 up
ip route replace '172.20.0.105/32' dev "${IF}"
ip -6 route replace 'fdce:1111:2222:dead::11/128' dev "${IF}"
echo "[wg] ${IF} applied"
wg show "${IF}"
