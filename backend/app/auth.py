import uuid

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User

_bearer = HTTPBearer()
_jwks_cache: dict[str, dict] = {}


async def _get_jwks(issuer: str) -> dict:
    if issuer in _jwks_cache:
        return _jwks_cache[issuer]
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{issuer}/.well-known/jwks.json")
        resp.raise_for_status()
    _jwks_cache[issuer] = resp.json()
    return _jwks_cache[issuer]


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials
    unauth = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(token, options={"verify_signature": False})
        issuer: str = unverified["iss"]
    except (jwt.PyJWTError, KeyError):
        raise unauth

    jwks = await _get_jwks(issuer)
    key_data = next((k for k in jwks["keys"] if k["kid"] == header.get("kid")), None)
    if not key_data:
        # Cache may be stale — bust and retry once
        _jwks_cache.pop(issuer, None)
        jwks = await _get_jwks(issuer)
        key_data = next((k for k in jwks["keys"] if k["kid"] == header.get("kid")), None)
    if not key_data:
        raise unauth

    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        payload = jwt.decode(token, public_key, algorithms=["RS256"])
    except jwt.PyJWTError:
        raise unauth

    clerk_user_id: str = payload["sub"]
    email: str | None = payload.get("email")

    result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(id=uuid.uuid4(), clerk_user_id=clerk_user_id, email=email)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user
