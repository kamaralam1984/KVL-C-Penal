"""Permission catalog and default role grants. Deny by default: anything not listed here is forbidden."""

RESOURCES: dict[str, tuple[str, ...]] = {
    "users": ("read", "write", "delete"),
    "roles": ("read", "manage"),
    "tenants": ("read", "write", "manage"),
    "members": ("read", "write", "delete"),
    "servers": ("read", "write", "manage"),
    "monitoring": ("read", "write"),
    "websites": ("read", "write", "delete", "manage"),
    "domains": ("read", "write", "delete", "manage"),
    "apps": ("read", "write", "delete", "manage"),
    "databases": ("read", "write", "delete", "manage"),
    "ssl": ("read", "write", "manage"),
    "dns": ("read", "write", "manage"),
    "files": ("read", "write", "delete"),
    "backups": ("read", "write", "restore", "manage"),
    "plans": ("read", "write", "manage"),
    "subscriptions": ("read", "write", "manage"),
    "billing": ("read", "write", "manage"),
    "resellers": ("read", "write", "manage"),
    "clients": ("read", "write", "delete"),
    "packages": ("read", "write", "delete"),
    "commissions": ("read", "manage"),
    "branding": ("read", "write"),
    "tickets": ("read", "write", "manage"),
    "kb": ("read", "write", "manage"),
    "announcements": ("read", "write", "manage"),
    "automation": ("read", "manage"),
    "apikeys": ("read", "write", "manage"),
    "webhooks": ("read", "write", "manage"),
    "security": ("read", "manage"),
    "audit": ("read",),
    "settings": ("read", "write"),
    "staff": ("read", "write", "manage"),
}

ALL_PERMISSIONS: list[str] = [f"{r}:{a}" for r, acts in RESOURCES.items() for a in acts]


def _p(*codes: str) -> set[str]:
    unknown = [c for c in codes if c not in ALL_PERMISSIONS]
    assert not unknown, f"unknown permissions: {unknown}"
    return set(codes)


ROLE_DEFS: dict[str, dict] = {
    "super_admin": {"label": "Platform Owner / Super Admin", "scope": "platform", "privileged": True, "perms": set(ALL_PERMISSIONS)},
    "hosting_admin": {
        "label": "Hosting Admin",
        "scope": "platform",
        "privileged": True,
        "perms": _p(
            "tenants:read", "members:read", "servers:read", "servers:write", "monitoring:read", "monitoring:write",
            "websites:read", "websites:write", "websites:delete", "websites:manage",
            "domains:read", "domains:write", "domains:delete", "domains:manage",
            "apps:read", "apps:write", "apps:delete", "apps:manage",
            "databases:read", "databases:write", "databases:delete", "databases:manage",
            "ssl:read", "ssl:write", "ssl:manage", "dns:read", "dns:write",
            "files:read", "files:write", "files:delete",
            "backups:read", "backups:write", "backups:restore", "backups:manage",
            "tickets:read", "tickets:write", "kb:read", "announcements:read",
        ),
    },
    "support": {
        "label": "Support Staff",
        "scope": "platform",
        "privileged": False,
        "perms": _p(
            "tenants:read", "members:read", "users:read", "websites:read", "domains:read", "monitoring:read",
            "tickets:read", "tickets:write", "tickets:manage", "kb:read", "kb:write", "announcements:read",
            "subscriptions:read", "billing:read",
        ),
    },
    "customer": {
        "label": "Customer",
        "scope": "tenant",
        "privileged": False,
        "perms": _p(
            "tenants:read", "tenants:write",
            "websites:read", "websites:write", "domains:read", "domains:write", "apps:read", "apps:write", "apps:manage",
            "databases:read", "databases:write", "ssl:read", "dns:read", "files:read", "files:write", "files:delete",
            "backups:read", "backups:write", "backups:restore", "billing:read", "billing:write", "subscriptions:read", "subscriptions:write",
            "tickets:read", "tickets:write", "kb:read", "announcements:read", "members:read", "monitoring:read",
            "apikeys:read", "apikeys:write", "webhooks:read", "webhooks:write",
        ),
    },
    "reseller": {
        "label": "Reseller / Agency",
        "scope": "reseller",
        "privileged": False,
        "perms": _p(
            "clients:read", "clients:write", "clients:delete", "packages:read", "packages:write", "packages:delete",
            "commissions:read", "branding:read", "branding:write", "tenants:read", "websites:read", "domains:read",
            "tickets:read", "tickets:write", "kb:read", "announcements:read", "billing:read", "plans:read",
        ),
    },
}

PRIVILEGED_ROLES = {n for n, d in ROLE_DEFS.items() if d["privileged"]}
