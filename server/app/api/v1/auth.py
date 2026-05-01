from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import write_audit_event
from app.core.auth import issue_access_token, issue_refresh_token, verify_password
from app.core.rate_limit import limiter
from app.core.security import current_user
from app.db.models import ActorType, User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"


class MeResponse(BaseModel):
    id: str
    email: str
    role: str
    tenant_id: str


# Rate limit per spec §6.6: 10 login attempts per minute per IP, mitigates
# credential-stuffing without locking out a user behind a NAT.
@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(
    request: Request,
    body: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    user = db.query(User).filter(User.email == body.email, User.is_active.is_(True)).one_or_none()
    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    user.last_login_at = datetime.now(timezone.utc)
    write_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_type=ActorType.USER,
        action="auth.login",
        target_type="user",
        target_id=str(user.id),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    return TokenResponse(
        access_token=issue_access_token(user.id, user.tenant_id, user.role.value),
        refresh_token=issue_refresh_token(user.id, user.tenant_id, user.role.value),
    )


@router.get("/me", response_model=MeResponse)
def me(user: Annotated[User, Depends(current_user)]) -> MeResponse:
    return MeResponse(
        id=str(user.id), email=user.email, role=user.role.value, tenant_id=str(user.tenant_id)
    )
