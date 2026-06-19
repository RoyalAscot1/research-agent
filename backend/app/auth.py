import uuid

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.logging_config import get_logger
from app.models.models import User

_bearer = HTTPBearer(auto_error=False)
_jwks_cache: dict[str, dict] = {}
log = get_logger(__name__)


async def _get_jwks(issuer: str) -> dict:
    if issuer in _jwks_cache:
        return _jwks_cache[issuer]
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{issuer}/.well-known/jwks.json")
        resp.raise_for_status()
    _jwks_cache[issuer] = resp.json()
    return _jwks_cache[issuer]


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    unauth = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # With auto_error=False, a missing/blank Authorization header yields None here
    # rather than HTTPBearer raising a 403 — return a 401 to match the rest of this
    # function (and the frontend's "session expired" handling).
    if credentials is None:
        log.warning("auth.rejected", reason="missing_token")
        raise unauth
    token = credentials.credentials

    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(token, options={"verify_signature": False})
        issuer: str = unverified["iss"]
    except (jwt.PyJWTError, KeyError):
        log.warning("auth.rejected", reason="malformed_token")
        raise unauth from None

    if issuer != settings.clerk_issuer:
        log.warning("auth.rejected", reason="iss_mismatch", issuer=issuer)
        raise unauth

    jwks = await _get_jwks(issuer)
    key_data = next((k for k in jwks["keys"] if k["kid"] == header.get("kid")), None)
    if not key_data:
        # Cache may be stale — bust and retry once
        _jwks_cache.pop(issuer, None)
        jwks = await _get_jwks(issuer)
        key_data = next((k for k in jwks["keys"] if k["kid"] == header.get("kid")), None)
    if not key_data:
        log.warning("auth.rejected", reason="unknown_kid")
        raise unauth

    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
        )
    except jwt.PyJWTError:
        log.warning("auth.rejected", reason="bad_signature")
        raise unauth from None

    if payload.get("azp") not in settings.clerk_authorized_parties:
        log.warning("auth.rejected", reason="azp_mismatch", azp=payload.get("azp"))
        raise unauth

    clerk_user_id: str = payload["sub"]
    email: str | None = payload.get("email")

    # Hand the verified identity to the rate limiter's key function (app/rate_limit.py),
    # which keys per user rather than per IP.
    request.state.clerk_user_id = clerk_user_id

    result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user = result.scalar_one_or_none()

    if not user:
        stmt = (
            pg_insert(User)
            .values(id=uuid.uuid4(), clerk_user_id=clerk_user_id, email=email)
            .on_conflict_do_nothing(index_elements=["clerk_user_id"])
        )
        await db.execute(stmt)
        await db.commit()
        result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
        user = result.scalar_one_or_none()

    return user
