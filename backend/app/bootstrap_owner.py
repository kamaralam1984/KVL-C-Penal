"""Create the FIRST platform owner (super_admin) for staging/production. Safe to run in any ENV.

    cd backend && set -a && . /path/to/api.env && set +a
    .venv/bin/python -m app.bootstrap_owner --email owner@example.com --name "Owner Name"

The password is read from a hidden prompt (typed twice) or, for automation, from stdin with --password-stdin.
It is never accepted as a command-line argument (would leak into shell history/process list) and never printed.

Safety rules:
- Refuses to run if any active super_admin already exists (one-time bootstrap; further staff are invited in the panel).
- Applies the normal password policy.
- Runs the idempotent role/permission seed first, so it works on a freshly migrated, empty database.
- Writes an audit event. Does not create demo data, tenants or API keys.
After first login, enable 2FA (TOTP) for the owner in the panel.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.identity import Membership, Role, User
from app.seed import seed
from app.services.audit import audit
from app.services.auth import norm_email, now, validate_password


class BootstrapError(Exception):
    pass


def bootstrap_owner(db: Session, email: str, name: str, password: str) -> User:
    email = norm_email(email)
    name = name.strip()
    if "@" not in email or len(email) > 320 or not name or len(name) > 200:
        raise BootstrapError("A valid email and a non-empty name (max 200 chars) are required.")
    try:
        validate_password(password, email)
    except Exception as e:  # HTTPException with a user-facing message
        detail = getattr(e, "detail", None)
        raise BootstrapError(detail["message"] if isinstance(detail, dict) else str(e)) from e

    seed(db)
    role = db.scalar(select(Role).where(Role.name == "super_admin"))
    if role is None:
        raise BootstrapError("Role super_admin is missing; run migrations (alembic upgrade head) first.")
    existing_owner = db.scalar(
        select(Membership).where(Membership.role_id == role.id, Membership.status == "active", Membership.tenant_id.is_(None))
    )
    if existing_owner is not None:
        db.rollback()
        raise BootstrapError("An active platform owner already exists; bootstrap is a one-time operation.")
    if db.scalar(select(User).where(User.email == email)) is not None:
        db.rollback()
        raise BootstrapError("A user with that email already exists.")

    user = User(email=email, name=name, password_hash=hash_password(password), email_verified_at=now())
    db.add(user)
    db.flush()
    db.add(Membership(user_id=user.id, role_id=role.id))
    audit(db, "owner.bootstrapped", actor=user.id, target_type="user", target_id=user.id, detail={"via": "cli"})
    db.commit()
    return user


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
    else:
        pw = getpass.getpass("Owner password: ")
        if getpass.getpass("Repeat password: ") != pw:
            raise BootstrapError("Passwords do not match.")
    return pw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create the first platform owner (one-time).")
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--password-stdin", action="store_true", help="read the password from stdin (first line) instead of prompting")
    args = ap.parse_args(argv)
    try:
        pw = _read_password(args.password_stdin)
        with SessionLocal() as db:
            user = bootstrap_owner(db, args.email, args.name, pw)
            print(f"Owner created: {user.email}. Log in, then enable 2FA.")
    except BootstrapError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
