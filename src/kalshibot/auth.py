from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def current_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def load_private_key(key_path: Path) -> rsa.RSAPrivateKey:
    with key_path.open("rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None,
            backend=default_backend(),
        )

    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise TypeError("KALSHI_PRIVATE_KEY_PATH must point to an RSA private key")

    return private_key


def signature_payload(timestamp: str, method: str, path: str) -> bytes:
    path_without_query = path.split("?", 1)[0]
    return f"{timestamp}{method.upper()}{path_without_query}".encode("utf-8")


def sign_request(
    private_key: rsa.RSAPrivateKey,
    timestamp: str,
    method: str,
    path: str,
) -> str:
    signature = private_key.sign(
        signature_payload(timestamp, method, path),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def auth_headers(
    api_key_id: str,
    private_key: rsa.RSAPrivateKey,
    method: str,
    path: str,
    timestamp: str | None = None,
) -> dict[str, str]:
    timestamp = timestamp or current_timestamp_ms()
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": sign_request(private_key, timestamp, method, path),
    }
