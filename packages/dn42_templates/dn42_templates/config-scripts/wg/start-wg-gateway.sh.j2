#!/usr/bin/env bash
set -euo pipefail

SECRET_KEY_FILE="/run/dn42-control/secrets/node.key"

# 容器首次启动与 agent 推送密钥之间存在天然时序：apply 脚本需要把
# secret:// 占位符解析成真实私钥。若 wireguard 配置引用了 secret://
# 而密钥还没到，就地等待（agent 部署成功后立即经 Docker API 推送），
# 避免启动即失败进入 restart 循环。
if grep -ls 'secret://' /etc/wireguard/*.conf >/dev/null 2>&1; then
  for _ in $(seq 1 120); do
    [ -f "${SECRET_KEY_FILE}" ] && break
    echo "[wg] waiting for node key ..."
    sleep 1
  done
  if [ ! -f "${SECRET_KEY_FILE}" ]; then
    echo "[wg] node key not delivered in time" >&2
    exit 1
  fi
fi

/opt/dn42/scripts/wg/apply-all-wg.sh
echo "[wg] gateway ready"
exec sleep infinity
