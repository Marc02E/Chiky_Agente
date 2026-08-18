import time

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from personal_ai_secretary.shared.auth import require_bearer_token
from personal_ai_secretary.shared.config import get_settings


def test_auth_disabled_returns_development_user(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", False)
    assert require_bearer_token() == {"sub": "development-user"}


def test_missing_bearer_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(None)
    assert exc.value.status_code == 401


def test_valid_bearer_token_is_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    token = jwt.encode(
        {
            "sub": "user-123",
            "aud": settings.jwt_audience,
            "iss": settings.jwt_issuer,
            "exp": int(time.time()) + 3600,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    claims = require_bearer_token(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    )
    assert claims["sub"] == "user-123"


def test_expired_bearer_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    token = jwt.encode(
        {
            "sub": "user-123",
            "aud": settings.jwt_audience,
            "iss": settings.jwt_issuer,
            "exp": int(time.time()) - 3600,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
    assert exc.value.status_code == 401


def test_token_without_exp_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    token = jwt.encode(
        {"sub": "user-123", "aud": settings.jwt_audience, "iss": settings.jwt_issuer},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
    assert exc.value.status_code == 401


def test_invalid_bearer_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    with pytest.raises(HTTPException) as exc:
        require_bearer_token(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid")
        )
    assert exc.value.status_code == 401
