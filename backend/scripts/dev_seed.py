"""Dev-only: create demo accounts for every role so the UI can be explored locally. Refuses to run outside ENV=dev."""
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.identity import Membership, Role, User  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services.auth import now  # noqa: E402
from app.services.tenants import provision_tenant  # noqa: E402

if get_settings().env != "dev":
    sys.exit("dev_seed refuses to run outside ENV=dev")

CRED_FILE = Path(__file__).resolve().parents[1] / ".dev-credentials"
ACCOUNTS = [
    ("owner@kvl.example.com", "Platform Owner", "super_admin"),
    ("hosting@kvl.example.com", "Hosting Admin", "hosting_admin"),
    ("support@kvl.example.com", "Support Agent", "support"),
    ("customer@kvl.example.com", "Demo Customer", "customer"),
    ("reseller@kvl.example.com", "Demo Reseller", "reseller"),
]


def main() -> None:
    password = f"Dev-{secrets.token_urlsafe(9)}1"
    with SessionLocal() as db:
        seed(db)
        lines = []
        for email, name, role in ACCOUNTS:
            if db.scalar(select(User).where(User.email == email)):
                continue
            u = User(email=email, name=name, password_hash=hash_password(password), email_verified_at=now())
            db.add(u)
            db.flush()
            r = db.scalar(select(Role).where(Role.name == role))
            if r.scope == "tenant":
                provision_tenant(db, "Demo Hosting Co", u.id, email, status="active", actor=u.id)
            else:
                db.add(Membership(user_id=u.id, role_id=r.id))
            lines.append(f"{role:14} {email}")
        db.commit()
    if lines:
        CRED_FILE.write_text("DEV ONLY credentials (same password for all)\npassword: " + password + "\n" + "\n".join(lines) + "\n")
        CRED_FILE.chmod(0o600)
        print(f"created {len(lines)} accounts; credentials in {CRED_FILE}")
    else:
        print("accounts already exist; credentials unchanged")


main()
