from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.auth import decode_token
from app.db.models import ApiKey, User, UserRole
from app.db.session import get_db

_bearer = HTTPBearer(auto_error=False)
_ph = PasswordHasher()

# `rk_` + 8-char prefix + `_` + 32-char secret tail = 44 chars total.
# The first 8 characters after `rk_` are the lookup prefix used to fetch
# the single matching ApiKey row before running Argon2 verification.
API_KEY_PREFIX = "rk_"
API_KEY_LOOKUP_LEN = 8
API_KEY_MIN_LEN = len(API_KEY_PREFIX) + API_KEY_LOOKUP_LEN + 16  # safety floor


def _unauthorized(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str = "forbidden") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _resolve_api_key(db: Session, plaintext: str) -> User:
    if not plaintext.startswith(API_KEY_PREFIX) or len(plaintext) < API_KEY_MIN_LEN:
        raise _unauthorized("invalid api key format")
    prefix = plaintext[len(API_KEY_PREFIX) : len(API_KEY_PREFIX) + API_KEY_LOOKUP_LEN]
    row = (
        db.query(ApiKey)
        .filter(ApiKey.lookup_prefix == prefix, ApiKey.revoked_at.is_(None))
        .one_or_none()
    )
    if not row:
        raise _unauthorized("invalid api key")
    try:
        _ph.verify(row.hashed_key, plaintext)
    except VerifyMismatchError as e:
        raise _unauthorized("invalid api key") from e
    user = (
        db.query(User)
        .filter(User.tenant_id == row.tenant_id, User.is_active.is_(True))
        .order_by(User.created_at)
        .first()
    )
    if not user:
        raise _unauthorized("api key has no associated user")
    return user


async def current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    api_key_header = request.headers.get("x-api-key")
    if api_key_header:
        return _resolve_api_key(db, api_key_header)

    if not creds or not creds.credentials:
        raise _unauthorized()
    try:
        claims = decode_token(creds.credentials)
    except ValueError as e:
        raise _unauthorized(str(e)) from e
    if claims.type != "access":
        raise _unauthorized("wrong token type")
    try:
        user_uuid = UUID(claims.sub)
    except ValueError as e:
        raise _unauthorized("invalid subject") from e
    user = db.get(User, user_uuid)
    if not user or not user.is_active:
        raise _unauthorized("user inactive or missing")
    return user


def require_role(*allowed: UserRole):
    async def _dep(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in allowed:
            raise _forbidden(f"role {user.role} not in {[r.value for r in allowed]}")
        return user

    return _dep
