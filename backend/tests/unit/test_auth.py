"""get_current_user — the Phase 1 auth-bypass fix, including forged-token rejection.

The interesting case is `test_forged_issuer_is_rejected`: an attacker self-signs a token
and points `iss` at a JWKS they control. The fix pins `iss` to settings.clerk_issuer
BEFORE fetching JWKS, so the forgery never gets its key trusted.
"""

import json
import time
import uuid
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app import auth
from app.config import settings
from tests.conftest import FakeResult, FakeSession, make_user


def _keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk_from(private_key, kid):
    pub_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    pub_jwk["kid"] = kid
    return pub_jwk


def _token(private_key, *, kid="test-key", **claims):
    payload = {
        "iss": settings.clerk_issuer,
        "azp": settings.clerk_authorized_parties[0],
        "sub": "user_clerk_123",
        "email": "person@example.com",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    payload.update(claims)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


def _creds(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.fixture(autouse=True)
def _clear_jwks_cache():
    auth._jwks_cache.clear()
    yield
    auth._jwks_cache.clear()


async def test_missing_credentials_returns_401():
    with pytest.raises(HTTPException) as exc:
        await get_current_user_call(credentials=None, db=FakeSession())
    assert exc.value.status_code == 401


async def test_forged_issuer_is_rejected(monkeypatch):
    # Attacker signs with their own key and sets iss to a domain they control.
    attacker_key = _keypair()
    token = _token(attacker_key, iss="https://attacker.example")

    # Even if their JWKS endpoint would serve the matching key, iss is checked first.
    called = {"jwks": False}

    async def spy_jwks(_issuer):
        called["jwks"] = True
        return {"keys": [_jwk_from(attacker_key, "test-key")]}

    monkeypatch.setattr(auth, "_get_jwks", spy_jwks)

    with pytest.raises(HTTPException) as exc:
        await get_current_user_call(credentials=_creds(token), db=FakeSession())
    assert exc.value.status_code == 401
    assert called["jwks"] is False  # rejected before any JWKS fetch


async def test_bad_signature_is_rejected(monkeypatch):
    # Token signed with key A, but the JWKS serves key B's public key.
    signing_key = _keypair()
    other_key = _keypair()
    token = _token(signing_key)

    async def jwks(_issuer):
        return {"keys": [_jwk_from(other_key, "test-key")]}

    monkeypatch.setattr(auth, "_get_jwks", jwks)

    with pytest.raises(HTTPException) as exc:
        await get_current_user_call(credentials=_creds(token), db=FakeSession())
    assert exc.value.status_code == 401


async def test_azp_mismatch_is_rejected(monkeypatch):
    key = _keypair()
    token = _token(key, azp="https://evil.example")

    async def jwks(_issuer):
        return {"keys": [_jwk_from(key, "test-key")]}

    monkeypatch.setattr(auth, "_get_jwks", jwks)

    with pytest.raises(HTTPException) as exc:
        await get_current_user_call(credentials=_creds(token), db=FakeSession())
    assert exc.value.status_code == 401


async def test_garbage_token_is_rejected():
    with pytest.raises(HTTPException) as exc:
        await get_current_user_call(credentials=_creds("not.a.jwt"), db=FakeSession())
    assert exc.value.status_code == 401


async def test_valid_token_returns_user(monkeypatch):
    key = _keypair()
    token = _token(key)

    async def jwks(_issuer):
        return {"keys": [_jwk_from(key, "test-key")]}

    monkeypatch.setattr(auth, "_get_jwks", jwks)

    # User doesn't exist yet -> upsert path: select(None) -> insert -> select(user).
    existing_user = make_user(user_id=uuid.uuid4(), clerk_user_id="user_clerk_123")
    db = FakeSession(
        execute_results=[
            FakeResult(scalar_one_or_none=None),  # initial lookup misses
            FakeResult(),  # the ON CONFLICT insert
            FakeResult(scalar_one_or_none=existing_user),  # re-select after insert
        ]
    )

    user = await get_current_user_call(credentials=_creds(token), db=db)

    assert user is existing_user
    assert db.committed is True


# get_current_user is a FastAPI dependency; call its underlying coroutine directly.
# It writes the verified id to request.state for the rate limiter, so pass a fake request.
async def get_current_user_call(*, credentials, db):
    request = SimpleNamespace(state=SimpleNamespace())
    return await auth.get_current_user(request=request, credentials=credentials, db=db)
