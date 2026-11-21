"""Signed anonymous browser identity for the demo UI.

This prevents guessing/changing owner UUIDs. It is a bearer credential, not a
replacement for corporate SSO: anyone who steals the token can impersonate it.
"""
from __future__ import annotations

import hashlib
import hmac
from uuid import UUID, uuid4

from fastapi import Header, HTTPException

from app.config import settings


def validate_client_auth_config() -> None:
    _secret()


def issue_client_token() -> tuple[str, str]:
    owner_id = str(uuid4())
    signature = hmac.new(_secret(), owner_id.encode(), hashlib.sha256).hexdigest()
    return owner_id, f"{owner_id}.{signature}"


def _secret() -> bytes:
    configured = settings.client_token_secret.get_secret_value() or settings.postgres_password.get_secret_value()
    if len(configured) < 32:
        raise RuntimeError("Set CLIENT_TOKEN_SECRET (32+ chars) or use a POSTGRES_PASSWORD of at least 32 characters")
    return configured.encode("utf-8")


def verify_client_token(token: str) -> str:
    try:
        owner_raw, signature = token.split(".", 1)
        owner_id = str(UUID(owner_raw))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid client token") from exc
    expected = hmac.new(_secret(), owner_id.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid client token")
    return owner_id


def client_owner(x_client_token: str = Header(..., alias="X-Client-Token")) -> str:
    return verify_client_token(x_client_token)
