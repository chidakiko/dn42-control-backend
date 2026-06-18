from __future__ import annotations

"""dn42_common.crypto 单测：WG 密钥生成 + 公钥托管封装/解封。"""

import pytest

from dn42_common import (
    derive_wireguard_public_key,
    generate_recovery_keypair,
    generate_wireguard_keypair,
    is_wireguard_key,
    recovery_key_fingerprint,
    seal_to_recovery_key,
    unseal_with_recovery_key,
)


def test_generated_wireguard_keys_are_wg_compatible() -> None:
    private, public = generate_wireguard_keypair()
    assert is_wireguard_key(private)
    assert is_wireguard_key(public)
    assert private != public


def test_public_key_derivation_is_deterministic_and_matches_keygen() -> None:
    private, public = generate_wireguard_keypair()
    assert derive_wireguard_public_key(private) == public
    # 同一私钥多次推导稳定。
    assert derive_wireguard_public_key(private) == derive_wireguard_public_key(private)


def test_derive_rejects_malformed_private_key() -> None:
    with pytest.raises(ValueError):
        derive_wireguard_public_key("not-base64!!!")
    with pytest.raises(ValueError):
        derive_wireguard_public_key("c2hvcnQ=")  # 合法 base64 但非 32 字节


def test_escrow_roundtrip_without_passphrase() -> None:
    private_pem, public_pem = generate_recovery_keypair()
    wg_private, _wg_public = generate_wireguard_keypair()
    blob = seal_to_recovery_key(wg_private.encode("ascii"), public_pem)
    assert blob != wg_private  # 密文不是明文
    recovered = unseal_with_recovery_key(blob, private_pem).decode("ascii")
    assert recovered == wg_private


def test_escrow_roundtrip_with_passphrase() -> None:
    private_pem, public_pem = generate_recovery_keypair(passphrase=b"s3cret")
    wg_private, _ = generate_wireguard_keypair()
    blob = seal_to_recovery_key(wg_private.encode("ascii"), public_pem)
    recovered = unseal_with_recovery_key(blob, private_pem, passphrase=b"s3cret").decode("ascii")
    assert recovered == wg_private


def test_unseal_with_wrong_passphrase_fails() -> None:
    private_pem, public_pem = generate_recovery_keypair(passphrase=b"right")
    blob = seal_to_recovery_key(b"x", public_pem)
    with pytest.raises((ValueError, TypeError)):
        unseal_with_recovery_key(blob, private_pem, passphrase=b"wrong")


def test_unseal_with_unrelated_key_fails() -> None:
    _p1, public_pem = generate_recovery_keypair()
    other_private, _ = generate_recovery_keypair()
    blob = seal_to_recovery_key(b"payload", public_pem)
    with pytest.raises(Exception):
        unseal_with_recovery_key(blob, other_private)


def test_recovery_fingerprint_is_stable_and_prefixed() -> None:
    _private, public_pem = generate_recovery_keypair()
    fp = recovery_key_fingerprint(public_pem)
    assert fp.startswith("sha256:")
    assert recovery_key_fingerprint(public_pem) == fp
