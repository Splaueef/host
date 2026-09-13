"""Request signing primitives shared by the service, CLI tests and clients."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass


PROTOCOL = "HIKKA-HUB-V1"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
KEY_ID_RE = re.compile(r"^hk_[A-Za-z0-9_-]{8,48}$")
INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class ParsedAuthorization:
    key_id: str
    signature: str


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def derive_hmac_key(secret: str) -> bytes:
    """Derive a fixed-size HMAC key without ever sending the secret itself."""
    if not isinstance(secret, str) or len(secret) < 32:
        raise ValueError("secret must contain at least 32 characters")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def canonical_request(
    method: str,
    target: str,
    timestamp: str,
    nonce: str,
    instance_id: str,
    owner_id: str,
    content_hash: str,
) -> bytes:
    values = (
        PROTOCOL,
        method.upper(),
        target,
        timestamp,
        nonce,
        instance_id,
        owner_id,
        content_hash.lower(),
    )
    if any("\n" in value or "\r" in value for value in values):
        raise ValueError("canonical fields cannot contain newlines")
    return "\n".join(values).encode("utf-8")


def sign_with_key(
    derived_key: bytes,
    method: str,
    target: str,
    timestamp: str,
    nonce: str,
    instance_id: str,
    owner_id: str,
    content_hash: str,
) -> str:
    canonical = canonical_request(
        method,
        target,
        timestamp,
        nonce,
        instance_id,
        owner_id,
        content_hash,
    )
    return hmac.new(derived_key, canonical, hashlib.sha256).hexdigest()


def sign_with_secret(
    secret: str,
    method: str,
    target: str,
    timestamp: str,
    nonce: str,
    instance_id: str,
    owner_id: str,
    content_hash: str,
) -> str:
    return sign_with_key(
        derive_hmac_key(secret),
        method,
        target,
        timestamp,
        nonce,
        instance_id,
        owner_id,
        content_hash,
    )


def parse_authorization(value: str) -> ParsedAuthorization:
    prefix = "Hikka-HMAC "
    if not value.startswith(prefix):
        raise ValueError("unsupported authorization scheme")
    token = value[len(prefix) :]
    key_id, separator, signature = token.partition(":")
    if not separator or not KEY_ID_RE.fullmatch(key_id):
        raise ValueError("invalid key id")
    if len(signature) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in signature):
        raise ValueError("invalid signature")
    return ParsedAuthorization(key_id=key_id, signature=signature.lower())

