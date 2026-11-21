from fastapi import HTTPException
from pydantic import SecretStr
import pytest

from app.core import client_auth


def test_signed_client_token_roundtrip(monkeypatch):
    monkeypatch.setattr(client_auth.settings, "client_token_secret", SecretStr("x" * 40))
    owner_id, token = client_auth.issue_client_token()
    assert client_auth.verify_client_token(token) == owner_id


def test_tampered_client_token_rejected(monkeypatch):
    monkeypatch.setattr(client_auth.settings, "client_token_secret", SecretStr("x" * 40))
    _, token = client_auth.issue_client_token()
    with pytest.raises(HTTPException):
        client_auth.verify_client_token(token[:-1] + ("0" if token[-1] != "0" else "1"))
