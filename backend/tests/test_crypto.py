import base64

import pytest

from app.core.secrets.crypto import DecryptionError, Encrypted, Keyring, KeyringError, secret_hint

K1 = base64.b64encode(b"1" * 32).decode()
K2 = base64.b64encode(b"2" * 32).decode()


def test_roundtrip_json() -> None:
    kr = Keyring.from_env_value(f"v1:{K1}")
    enc = kr.encrypt_json({"password": "s3cret"}, "credential:a:read")
    assert b"s3cret" not in enc.blob
    assert kr.decrypt_json(enc, "credential:a:read") == {"password": "s3cret"}


def test_nonce_is_random() -> None:
    kr = Keyring.from_env_value(f"v1:{K1}")
    assert kr.encrypt(b"x", "a").blob != kr.encrypt(b"x", "a").blob


def test_aad_binds_ciphertext_to_owner() -> None:
    kr = Keyring.from_env_value(f"v1:{K1}")
    enc = kr.encrypt(b"x", "credential:a:read")
    with pytest.raises(DecryptionError):
        kr.decrypt(enc, "credential:a:write")


def test_tampering_detected() -> None:
    kr = Keyring.from_env_value(f"v1:{K1}")
    enc = kr.encrypt(b"x", "a")
    tampered = Encrypted(enc.key_version, enc.blob[:-1] + bytes([enc.blob[-1] ^ 1]))
    with pytest.raises(DecryptionError):
        kr.decrypt(tampered, "a")


def test_rotation_old_key_still_decrypts_new_key_encrypts() -> None:
    old = Keyring.from_env_value(f"v1:{K1}")
    enc_old = old.encrypt(b"x", "a")
    rotated = Keyring.from_env_value(f"v2:{K2},v1:{K1}")
    assert rotated.decrypt(enc_old, "a") == b"x"
    assert rotated.needs_reencrypt(enc_old)
    assert rotated.encrypt(b"y", "a").key_version == "v2"


def test_missing_key_version() -> None:
    enc = Keyring.from_env_value(f"v1:{K1}").encrypt(b"x", "a")
    with pytest.raises(DecryptionError):
        Keyring.from_env_value(f"v2:{K2}").decrypt(enc, "a")


@pytest.mark.parametrize("value", ["", "v1", f"v1:{base64.b64encode(b'short').decode()}", "v1:!!notbase64"])
def test_invalid_keyring(value: str) -> None:
    with pytest.raises(KeyringError):
        Keyring.from_env_value(value)


def test_hint_only_for_long_secrets() -> None:
    assert secret_hint("abcdefghijkl") == "ijkl"
    assert secret_hint("short") == ""
