"""RedactLy admin CLI.

Usage examples:
    uv run python -m cli admin create --email me@example.com --password '...'
    uv run python -m cli migrate
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from app.config import get_settings
from app.core.auth import hash_password
from app.db.models import Tenant, User, UserRole
from app.db.session import SessionLocal

app = typer.Typer(no_args_is_help=True)
admin = typer.Typer(no_args_is_help=True, help="Administrative commands")
app.add_typer(admin, name="admin")


@app.command()
def migrate() -> None:
    """Apply outstanding Alembic migrations."""
    here = Path(__file__).parent
    rc = subprocess.call(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=here
    )
    raise typer.Exit(code=rc)


@admin.command("create")
def admin_create(
    email: str = typer.Option(..., help="Admin email address"),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    tenant_name: str = typer.Option("default", help="Tenant name; created if missing"),
) -> None:
    """Create the first admin user (idempotent on email)."""
    s = get_settings()
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.name == tenant_name).one_or_none()
        if not tenant:
            tenant = Tenant(
                name=tenant_name,
                settings={"retention_hours": s.default_tenant_retention_hours},
            )
            db.add(tenant)
            db.flush()

        user = (
            db.query(User)
            .filter(User.tenant_id == tenant.id, User.email == email)
            .one_or_none()
        )
        if user:
            user.hashed_password = hash_password(password)
            user.role = UserRole.ADMIN
            user.is_active = True
        else:
            user = User(
                tenant_id=tenant.id,
                email=email,
                hashed_password=hash_password(password),
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(user)
        db.commit()
        typer.echo(f"admin: {user.email} (tenant={tenant.name}, id={user.id})")


if __name__ == "__main__":
    app()
