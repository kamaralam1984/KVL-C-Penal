import os

os.environ["ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://kvl:kvl_dev_only_pw@127.0.0.1:54329/kvl_panel_test")

import uuid  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

import app.models  # noqa: E402,F401
from app.core import ratelimit  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.identity import Membership, Role, User  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services.tenants import provision_tenant  # noqa: E402

PW = "Correct-Horse-9"

_GRANTS_SQL = "select coalesce(md5(string_agg(role_id::text || permission_id::text, ',' order by role_id, permission_id)), '') from role_permissions"
_baseline: dict[str, str] = {}


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Build the schema through Alembic so migrations themselves are exercised."""
    with engine.begin() as c:
        c.execute(text("drop schema public cascade"))
        c.execute(text("create schema public"))
    if os.environ.get("USE_MIGRATIONS") == "1":
        command.upgrade(Config("alembic.ini"), "head")
    else:
        Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed(s)
    with engine.begin() as c:
        _baseline["grants"] = c.execute(text(_GRANTS_SQL)).scalar()
    yield


@pytest.fixture(autouse=True)
def _clean():
    with engine.begin() as c:
        keep = {"alembic_version", "roles", "permissions", "role_permissions", "platform_settings"}
        names = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables) if t.name not in keep)
        c.execute(text(f"truncate {names} restart identity cascade"))
    # Role grants are seeded once, but some tests edit them (role-permission changes). Restore the seeded state
    # so one test can never change what another test's roles are allowed to do.
    from app.core.deps import invalidate_role_cache

    with engine.begin() as c:
        drifted = c.execute(text(_GRANTS_SQL)).scalar() != _baseline["grants"]
        if drifted:
            c.execute(text("truncate role_permissions"))
    if drifted:
        with SessionLocal() as s:
            seed(s)
    invalidate_role_cache()
    ratelimit.reset_all()
    yield


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def make_client():
    clients: list[TestClient] = []

    def _make() -> TestClient:
        c = TestClient(app, base_url="http://testserver")
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.close()


@pytest.fixture
def client(make_client):
    return make_client()


def login(client: TestClient, email: str, password: str = PW, **extra):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password, **extra})
    if r.status_code == 200:
        client.headers["X-CSRF-Token"] = client.cookies.get("kvl_csrf")
    return r


@pytest.fixture
def make_user(db):
    def _make(email: str, role: str = "customer", tenant=None, verified: bool = True, name: str = "Test User", tenant_name: str | None = None):
        u = User(email=email.lower(), name=name, password_hash=hash_password(PW), email_verified_at=None)
        if verified:
            from app.services.auth import now

            u.email_verified_at = now()
        db.add(u)
        db.flush()
        r = db.scalar(select(Role).where(Role.name == role))
        if r.scope == "tenant":
            t = tenant or provision_tenant(db, tenant_name or f"{email.split('@')[0]} Co", u.id, email, status="active")
            if tenant is not None:
                db.add(Membership(user_id=u.id, role_id=r.id, tenant_id=tenant.id))
            db.commit()
            return u, t
        db.add(Membership(user_id=u.id, role_id=r.id, tenant_id=None))
        db.commit()
        return u, None

    return _make


@pytest.fixture
def admin(make_user, client):
    u, _ = make_user("owner@kvl.example.com", "super_admin")
    assert login(client, "owner@kvl.example.com").status_code == 200
    return u


def new_id() -> str:
    return str(uuid.uuid4())
