import secrets
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import write_audit_event
from app.core.security import require_role
from app.db.models import ActorType, ApiKey, User, UserRole
from app.db.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"])

_ph = PasswordHasher()
_ALLOWED_SCOPES = {"jobs:create", "jobs:read", "admin:audit", "admin:users"}


class CreateApiKeyRequest(BaseModel):
    name: str
    scopes: list[str]


class CreateApiKeyResponse(BaseModel):
    id: str
    name: str
    scopes: list[str]
    plaintext_key: str  # shown exactly once


class ApiKeyRow(BaseModel):
    id: str
    name: str
    scopes: list[str]
    created_at: datetime
    revoked_at: datetime | None


@router.post(
    "/api-keys",
    response_model=CreateApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
def create_api_key(
    body: CreateApiKeyRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
) -> CreateApiKeyResponse:
    bad = set(body.scopes) - _ALLOWED_SCOPES
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown scopes: {sorted(bad)}")

    # Format: rk_<8-char prefix><urlsafe tail>. The 8-char prefix is stored
    # unhashed to allow O(1) lookup at auth time; see app/core/security.py.
    prefix = secrets.token_urlsafe(6)[:8]
    tail = secrets.token_urlsafe(32)
    plain = f"rk_{prefix}{tail}"
    row = ApiKey(
        tenant_id=user.tenant_id,
        name=body.name,
        lookup_prefix=prefix,
        hashed_key=_ph.hash(plain),
        scopes=body.scopes,
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    write_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_type=ActorType.USER,
        action="api_key.create",
        target_type="api_key",
        target_id=str(row.id),
        payload={"name": row.name, "scopes": row.scopes},
    )
    db.commit()
    return CreateApiKeyResponse(
        id=str(row.id), name=row.name, scopes=row.scopes, plaintext_key=plain
    )


@router.get("/api-keys", response_model=list[ApiKeyRow])
def list_api_keys(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
) -> list[ApiKeyRow]:
    rows = db.query(ApiKey).filter(ApiKey.tenant_id == user.tenant_id).all()
    return [
        ApiKeyRow(
            id=str(r.id),
            name=r.name,
            scopes=r.scopes,
            created_at=r.created_at,
            revoked_at=r.revoked_at,
        )
        for r in rows
    ]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
) -> None:
    row = db.get(ApiKey, key_id)
    if not row or row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        write_audit_event(
            db,
            tenant_id=user.tenant_id,
            actor_id=user.id,
            actor_type=ActorType.USER,
            action="api_key.revoke",
            target_type="api_key",
            target_id=str(row.id),
        )
        db.commit()
