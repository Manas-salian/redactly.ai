from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import ActorType, AuditEvent


def write_audit_event(
    db: Session,
    *,
    tenant_id: UUID,
    actor_id: UUID | None,
    actor_type: ActorType,
    action: str,
    target_type: str,
    target_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit row.

    Caller must commit the surrounding session. We never write PII text into
    payload — caller is responsible for redacting/hashing sensitive fields
    before passing them in (see spec §5.7).
    """
    row = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        user_agent=user_agent,
        payload=payload or {},
    )
    db.add(row)
    db.flush()
    return row
