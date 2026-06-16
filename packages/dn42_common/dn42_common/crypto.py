from __future__ import annotations

"""WireGuard 密钥生成与"离线公钥托管"加密原语。

本模块承担两件事，都只调用经过审计的 `cryptography` 高层 API，**绝不**自行
实现椭圆曲线或填充运算：

1. **WireGuard 密钥对**：X25519，产出与 `wg genkey` / `wg pubkey` 逐字节兼容
   的 44 字符 base64 密钥。节点本地生成私钥，公钥推导后上报控制面。
2. **公钥托管（escrow）**：节点用运维持有的"恢复公钥"(RSA-OAEP) 把 WG 私钥
   封装成密文，存进控制面。控制面只存不解；只有离线保管的恢复私钥能解封。
   即便控制面被完整攻陷，也拿不到任何 WG 私钥明文。

实际运算依赖 `cryptography` 包，采用惰性导入：未安装时抛 `RuntimeError`，
使纯结构代码无需该依赖即可 import。
"""

import base64
import binascii

# RSA-OAEP(SHA-256) 在 4096 位密钥下可封装约 446 字节明文；WG 私钥 base64 仅 44
# 字节，单块绰绰有余。恢复密钥默认用此长度。
_RECOVERY_RSA_KEY_SIZE = 4096
_RECOVERY_RSA_PUBLIC_EXPONENT = 65537


def _require_cryptography():
    try:
        from cryptography.hazmat.primitives import hashes, serialization  # pyright: ignore[reportMissingImports]
        from cryptography.hazmat.primitives.asymmetric import padding, rsa, x25519  # pyright: ignore[reportMissingImports]
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise RuntimeError(
            "WireGuard 密钥与托管加密需要 'cryptography' 包；请安装后再使用 "
            "dn42_common.crypto"
        ) from exc
    return hashes, serialization, padding, rsa, x25519


def _oaep(padding, hashes):
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


# ---- WireGuard 密钥对 ----


def generate_wireguard_keypair() -> tuple[str, str]:
    """生成一对 WireGuard 密钥，返回 ``(private_b64, public_b64)``。

    与 ``wg genkey | wg pubkey`` 输出兼容：均为 32 字节裸密钥的标准 base64
    （44 字符）。
    """

    _, serialization, _, _, x25519 = _require_cryptography()
    private = x25519.X25519PrivateKey.generate()
    private_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return (
        base64.b64encode(private_raw).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def derive_wireguard_public_key(private_b64: str) -> str:
    """从 WireGuard 私钥（base64）推导其公钥（base64）。

    用于注册一致性校验与恢复后核对：节点上报的公钥永远派生自它真实持有的
    私钥，因此公钥比对天然也是"持有性"证明，无需额外签名。
    """

    _, serialization, _, _, x25519 = _require_cryptography()
    try:
        raw = base64.b64decode(private_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("wireguard private key is not valid base64") from exc
    if len(raw) != 32:
        raise ValueError("wireguard private key does not decode to 32 bytes")
    private = x25519.X25519PrivateKey.from_private_bytes(raw)
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(public_raw).decode("ascii")


# ---- 恢复密钥对（离线生成，供 CLI 用）----


def generate_recovery_keypair(
    passphrase: bytes | None = None,
) -> tuple[bytes, bytes]:
    """生成离线恢复用的 RSA 密钥对，返回 ``(private_pem, public_pem)``。

    私钥 PEM 在 ``passphrase`` 非空时以该口令加密（``BestAvailableEncryption``），
    使泄露的私钥文件单凭自身不可用——口令不落盘。公钥 PEM 可公开分发给节点。
    """

    _, serialization, _, rsa, _ = _require_cryptography()
    private = rsa.generate_private_key(
        public_exponent=_RECOVERY_RSA_PUBLIC_EXPONENT,
        key_size=_RECOVERY_RSA_KEY_SIZE,
    )
    if passphrase:
        encryption = serialization.BestAvailableEncryption(passphrase)
    else:
        encryption = serialization.NoEncryption()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        encryption,
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def recovery_key_fingerprint(public_pem: bytes | str) -> str:
    """返回恢复公钥的 SHA-256 指纹（``sha256:<hex[:32]>``），供分发时核对真实性。"""

    hashes, serialization, _, _, _ = _require_cryptography()
    from cryptography.hazmat.primitives.hashes import Hash  # pyright: ignore[reportMissingImports]

    pem_bytes = public_pem.encode("ascii") if isinstance(public_pem, str) else public_pem
    public = serialization.load_pem_public_key(pem_bytes)
    der = public.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = Hash(hashes.SHA256())
    digest.update(der)
    return "sha256:" + digest.finalize().hex()[:32]


# ---- 托管封装 / 解封 ----


def seal_to_recovery_key(plaintext: bytes, recovery_public_pem: bytes | str) -> str:
    """用恢复公钥(RSA-OAEP/SHA-256)封装明文，返回 base64 密文。

    节点用此封装自己的 WG 私钥。封装是单向的——只有持有配对恢复私钥的人能解。
    """

    hashes, serialization, padding, _, _ = _require_cryptography()
    pem_bytes = (
        recovery_public_pem.encode("ascii")
        if isinstance(recovery_public_pem, str)
        else recovery_public_pem
    )
    public = serialization.load_pem_public_key(pem_bytes)
    ciphertext = public.encrypt(plaintext, _oaep(padding, hashes))
    return base64.b64encode(ciphertext).decode("ascii")


def unseal_with_recovery_key(
    ciphertext_b64: str,
    recovery_private_pem: bytes | str,
    passphrase: bytes | None = None,
) -> bytes:
    """用离线恢复私钥解封托管密文，返回明文。

    **仅供离线恢复 CLI 使用**；控制面服务进程不得导入/调用此函数——它只存密文。
    """

    hashes, serialization, padding, _, _ = _require_cryptography()
    pem_bytes = (
        recovery_private_pem.encode("ascii")
        if isinstance(recovery_private_pem, str)
        else recovery_private_pem
    )
    private = serialization.load_pem_private_key(pem_bytes, password=passphrase)
    try:
        ciphertext = base64.b64decode(ciphertext_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("escrow ciphertext is not valid base64") from exc
    return private.decrypt(ciphertext, _oaep(padding, hashes))


__all__ = [
    "derive_wireguard_public_key",
    "generate_recovery_keypair",
    "generate_wireguard_keypair",
    "recovery_key_fingerprint",
    "seal_to_recovery_key",
    "unseal_with_recovery_key",
]
