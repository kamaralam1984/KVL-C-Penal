"""Idempotent seed: roles, permissions, role grants and default platform settings."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import invalidate_role_cache
from app.core.rbac import ALL_PERMISSIONS, ROLE_DEFS
from app.models.identity import Permission, Role, RolePermission
from app.models.tenant import PlatformSetting

DEFAULT_SETTINGS = {
    "trial_days": {"days": 14},
    "grace_days": {"days": 7},
    "retention_days": {"days": 30},
    "default_quotas": {"websites": 5, "domains": 10, "databases": 5, "apps": 5, "disk_mb": 10240, "bandwidth_gb": 100},
}


def seed(db: Session) -> None:
    existing_perms = {p.code: p for p in db.scalars(select(Permission))}
    for code in ALL_PERMISSIONS:
        if code not in existing_perms:
            p = Permission(code=code)
            db.add(p)
            existing_perms[code] = p
    existing_roles = {r.name: r for r in db.scalars(select(Role))}
    for name, d in ROLE_DEFS.items():
        if name not in existing_roles:
            r = Role(name=name, label=d["label"], scope=d["scope"], privileged=d["privileged"])
            db.add(r)
            existing_roles[name] = r
    db.flush()
    granted = {(rp.role_id, rp.permission_id) for rp in db.scalars(select(RolePermission))}
    for name, d in ROLE_DEFS.items():
        for code in d["perms"]:
            key = (existing_roles[name].id, existing_perms[code].id)
            if key not in granted:
                db.add(RolePermission(role_id=key[0], permission_id=key[1]))
    have = {s.key for s in db.scalars(select(PlatformSetting))}
    for k, v in DEFAULT_SETTINGS.items():
        if k not in have:
            db.add(PlatformSetting(key=k, value=v))
    db.commit()
    invalidate_role_cache()


if __name__ == "__main__":
    from app.core.db import SessionLocal

    with SessionLocal() as s:
        seed(s)
    print("seeded")
