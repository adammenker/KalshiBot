from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshibot.auth import auth_headers, signature_payload, sign_request


def test_signature_payload_strips_query_params() -> None:
    payload = signature_payload(
        "1703123456789",
        "get",
        "/trade-api/v2/portfolio/orders?limit=5",
    )

    assert payload == b"1703123456789GET/trade-api/v2/portfolio/orders"


def test_sign_request_creates_verifiable_signature() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    timestamp = "1703123456789"
    method = "GET"
    path = "/trade-api/v2/portfolio/balance"

    signature = sign_request(private_key, timestamp, method, path)

    public_key = private_key.public_key()
    public_key.verify(
        __import__("base64").b64decode(signature),
        signature_payload(timestamp, method, path),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )


def test_auth_headers_include_required_kalshi_headers() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    headers = auth_headers(
        "test-key-id",
        private_key,
        "GET",
        "/trade-api/v2/portfolio/balance",
        timestamp="1703123456789",
    )

    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1703123456789"
    assert headers["KALSHI-ACCESS-SIGNATURE"]
