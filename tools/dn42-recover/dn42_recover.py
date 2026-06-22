#!/usr/bin/env python3
from __future__ import annotations

"""离线 WireGuard 私钥托管恢复 CLI。

两件事，都在**离线运维机**上做，绝不在控制服务器上：

- ``keygen``：生成恢复用 RSA 密钥对。私钥用口令加密落 ``recovery-private.pem``，
  公钥落 ``recovery-public.pem`` 交给控制面分发给节点。
- ``recover``：用恢复私钥解封某条接口的托管密文（``wg_interfaces.private_key_escrow``），
  还原出原始 WG 私钥；可选校验它与控制面记录的公钥一致。

只依赖 ``dn42_common.crypto``——控制面服务进程绝不导入本脚本里的解封路径。
"""

import argparse
import getpass
import sys
from pathlib import Path

from dn42_common import (
    derive_wireguard_public_key,
    generate_recovery_keypair,
    recovery_key_fingerprint,
    unseal_with_recovery_key,
)

_PRIVATE_NAME = "recovery-private.pem"
_PUBLIC_NAME = "recovery-public.pem"


def _warn_offline() -> None:
    print(
        "⚠️  这是离线恢复工具：恢复私钥不得出现在控制服务器主机上。",
        file=sys.stderr,
    )


def cmd_keygen(args: argparse.Namespace) -> int:
    _warn_offline()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path = out_dir / _PRIVATE_NAME
    public_path = out_dir / _PUBLIC_NAME
    if private_path.exists() and not args.force:
        print(f"拒绝覆盖已存在的 {private_path}（加 --force 强制）", file=sys.stderr)
        return 2

    passphrase = _prompt_passphrase(confirm=True) if not args.no_passphrase else None
    private_pem, public_pem = generate_recovery_keypair(passphrase=passphrase)

    private_path.write_bytes(private_pem)
    private_path.chmod(0o600)
    public_path.write_bytes(public_pem)

    print(f"恢复私钥 → {private_path}  (0600，{'已口令加密' if passphrase else '未加密！'})")
    print(f"恢复公钥 → {public_path}")
    print(f"公钥指纹: {recovery_key_fingerprint(public_pem)}")
    print(
        "\n下一步：把恢复公钥配到控制面 "
        "DN42_CONTROL_RECOVERY_PUBLIC_KEY="
        f"{public_path}"
    )
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    _warn_offline()
    private_pem = Path(args.private_key).read_bytes()
    blob = (
        sys.stdin.read().strip()
        if args.escrow_file == "-"
        else Path(args.escrow_file).read_text(encoding="ascii").strip()
    )
    passphrase = None if args.no_passphrase else _prompt_passphrase(confirm=False)

    try:
        recovered = unseal_with_recovery_key(blob, private_pem, passphrase).decode("ascii")
    except Exception as exc:  # noqa: BLE001 - CLI 顶层兜底，给出可读错误
        print(f"解封失败：{exc}", file=sys.stderr)
        return 1

    if args.expect_public:
        derived = derive_wireguard_public_key(recovered)
        if derived != args.expect_public:
            print(
                "❌ 校验失败：解出的私钥推导公钥与 --expect-public 不一致；"
                "托管密文与记录不匹配。",
                file=sys.stderr,
            )
            return 1
        print("✅ 公钥校验通过：解出的私钥与控制面记录一致。", file=sys.stderr)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(recovered + "\n", encoding="ascii", newline="\n")
        out_path.chmod(0o600)
        print(f"恢复的 WG 私钥 → {out_path} (0600)", file=sys.stderr)
    else:
        # 默认打到 stdout，便于管道；提示信息走 stderr 不污染。
        print(recovered)
    return 0


def _prompt_passphrase(*, confirm: bool) -> bytes | None:
    pw = getpass.getpass("恢复私钥口令（留空表示无口令）: ")
    if not pw:
        return None
    if confirm:
        again = getpass.getpass("再次输入确认: ")
        if pw != again:
            raise SystemExit("两次口令不一致")
    return pw.encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dn42-recover", description="离线 WireGuard 私钥托管恢复工具"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    kg = sub.add_parser("keygen", help="生成离线恢复密钥对")
    kg.add_argument("--out-dir", default="secrets", help="输出目录（默认 secrets/）")
    kg.add_argument("--no-passphrase", action="store_true", help="不给私钥加口令（仅开发）")
    kg.add_argument("--force", action="store_true", help="覆盖已存在的恢复私钥")
    kg.set_defaults(func=cmd_keygen)

    rc = sub.add_parser("recover", help="用恢复私钥解封托管密文")
    rc.add_argument("--private-key", required=True, help="恢复私钥 PEM 路径")
    rc.add_argument(
        "--escrow-file", required=True, help="托管密文 base64 文件路径；'-' 表示从 stdin 读"
    )
    rc.add_argument("--expect-public", help="期望的 WG 公钥，用于校验恢复正确性")
    rc.add_argument("--out", help="把恢复出的 WG 私钥写入该文件（0600）；默认打 stdout")
    rc.add_argument("--no-passphrase", action="store_true", help="恢复私钥无口令")
    rc.set_defaults(func=cmd_recover)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
