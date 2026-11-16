"""AES-256-GCM encryption for connection secrets with a versioned keyring.

Format of ``INFRAOPS_MASTER_KEYS``: ``v2:<base64 key>,v1:<base64 key>``.
The first key encrypts; every key decrypts, which allows rotating the master key
without downtime (add the new key first, then re-encrypt with ``reencrypt``).

Ciphertext layout: ``nonce(12) || ciphertext+tag``. The AAD binds the ciphertext to
its owner (``credential:<profile_id>:<purpose>``) so it cannot be swapped between rows.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12


class KeyringError(Exception):
    pass


class DecryptionError(Exception):
    pass


@dataclass(frozen=True)
class Encrypted:
    key_version: str
    blob: bytes


class Keyring:
    def __init__(self, keys: dict[str, bytes], active: str) -> None:
        if active not in keys:
            raise KeyringError("Active key version missing from keyring")
        for version, key in keys.items():
            if len(key) != 32:
                raise KeyringError(f"Master key {version!r} must be 32 bytes (AES-256)")
        self._keys = keys
        self.active = active

    @classmethod
    def from_env_value(cls, value: str) -> Keyring:
        keys: dict[str, bytes] = {}
        order: list[str] = []
        for entry in (part.strip() for part in value.split(",")):
            if not entry:
                continue
            version, sep, b64 = entry.partition(":")
            if not sep or not version.isalnum():
                raise KeyringError("Master key entries must look like 'v1:<base64>'")
            try:
                keys[version] = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise KeyringError(f"Master key {version!r} is not valid base64") from exc
            order.append(version)
        if not order:
            raise KeyringError("INFRAOPS_MASTER_KEYS is empty")
        return cls(keys, order[0])

    @staticmethod
    def generate_key() -> str:
        return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()

    def encrypt(self, plaintext: bytes, aad: str) -> Encrypted:
        nonce = os.urandom(NONCE_SIZE)
        ct = AESGCM(self._keys[self.active]).encrypt(nonce, plaintext, aad.encode())
        return Encrypted(self.active, nonce + ct)

    def decrypt(self, enc: Encrypted, aad: str) -> bytes:
        key = self._keys.get(enc.key_version)
        if key is None:
            raise DecryptionError(f"Master key version {enc.key_version!r} is not loaded")
        nonce, ct = enc.blob[:NONCE_SIZE], enc.blob[NONCE_SIZE:]
        try:
            return AESGCM(key).decrypt(nonce, ct, aad.encode())
        except InvalidTag as exc:
            raise DecryptionError("Secret could not be decrypted (wrong key or tampered data)") from exc

    def encrypt_json(self, data: dict[str, Any], aad: str) -> Encrypted:
        return self.encrypt(json.dumps(data, separators=(",", ":"), sort_keys=True).encode(), aad)

    def decrypt_json(self, enc: Encrypted, aad: str) -> dict[str, Any]:
        result = json.loads(self.decrypt(enc, aad))
        if not isinstance(result, dict):
            raise DecryptionError("Decrypted secret is not an object")
        return result

    def needs_reencrypt(self, enc: Encrypted) -> bool:
        return enc.key_version != self.active


def secret_hint(value: str) -> str:
    """Last 4 characters for display (``••••abcd``); empty for short secrets to avoid leaking them."""
    return value[-4:] if len(value) >= 12 else ""


_keyring: Keyring | None = None


def get_keyring() -> Keyring:
    global _keyring
    if _keyring is None:
        from app.core.config import get_settings

        _keyring = Keyring.from_env_value(get_settings().master_keys.get_secret_value())
    return _keyring


def reset_keyring() -> None:
    global _keyring
    _keyring = None
