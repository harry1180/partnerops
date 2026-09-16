"""Idempotent demo seed: platform hierarchy, RBAC catalog, demo users,
branding defaults. Runs only when APP_ENV is local/test and refuses to touch
a non-empty platform.

Demo users (documented in docs/demo-credentials.md):
  demo password = SEED_DEMO_PASSWORD env (default Demo-2026-LocalOnly)
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.db.rls import set_bypass_scope, set_org_scope
from app.models.auth import Permission, Role, RolePermission, User, UserRoleAssignment
from app.models.branding import BrandingConfig
from app.models.org import Organization
from app.services.audit_service import record_audit
from app.services.authz import PERMISSIONS, ROLE_PERMISSIONS
from app.services.passwords import hash_password

settings = get_settings()

PLATFORM_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")

DEMO_USERS: list[tuple[str, str, str, Any]] = [
    ("admin@cloudpartnerops.example.com", "Ada Platform", "platform_admin", PLATFORM_ID),
    ("dist@northwind-distribution.example.com", "Dana Distributor", "distributor_admin", None),
    ("msp@northwind-msp.example.com", "Morgan Northwind", "msp_admin", None),
    ("msp@cascade-it.example.com", "Casey Cascade", "msp_admin", None),
    ("finops@northwind-msp.example.com", "Fiona Analyst", "finops_analyst", None),
    ("billing@northwind-msp.example.com", "Ben Billing", "billing_analyst", None),
    ("admin@acme-cloud.example.com", "Alex Acme Admin", "customer_admin", None),
    ("viewer@acme-cloud.example.com", "Wendy Acme Viewer", "customer_readonly", None),
    ("auditor@cloudpartnerops.example.com", "Sam Auditor", "auditor", None),
]

# org tree: platform → distributor → two MSPs; each MSP → customers; one
# customer org also hosts customer users.
ORG_TREE: dict[str, Any] = {
    "id": PLATFORM_ID,
    "kind": "platform",
    "name": "Cloud PartnerOps Platform",
    "children": [
        {
            "id": uuid.UUID("22222222-2222-4222-8222-222222222222"),
            "kind": "distributor",
            "name": "Northwind Distribution",
            "children": [
                {
                    "id": uuid.UUID("33333333-3333-4333-8333-333333333333"),
                    "kind": "reseller",
                    "name": "Northwind MSP",
                    "children": [
                        {"id": uuid.UUID("44444444-4444-4444-8444-444444444441"), "kind": "customer", "name": "Acme Cloud Co"},
                        {"id": uuid.UUID("44444444-4444-4444-8444-444444444442"), "kind": "customer", "name": "BlueRiver Systems"},
                        {"id": uuid.UUID("44444444-4444-4444-8444-444444444443"), "kind": "customer", "name": "Cobalt Labs"},
                    ],
                },
                {
                    "id": uuid.UUID("33333333-3333-4333-8333-333333333334"),
                    "kind": "reseller",
                    "name": "Cascade IT Partners",
                    "children": [
                        {"id": uuid.UUID("44444444-4444-4444-8444-444444444445"), "kind": "customer", "name": "Delta Manufacturing"},
                        {"id": uuid.UUID("44444444-4444-4444-8444-444444444446"), "kind": "customer", "name": "Evergreen Retail"},
                    ],
                },
            ],
        },
        {
            # second distributor keeps tenant-isolation journeys honest
            "id": uuid.UUID("22222222-2222-4222-8222-222222222299"),
            "kind": "distributor",
            "name": "Southbridge Partners",
            "children": [
                {
                    "id": uuid.UUID("33333333-3333-4333-8333-333333333399"),
                    "kind": "reseller",
                    "name": "Southbridge MSP",
                    "children": [
                        {"id": uuid.UUID("44444444-4444-4444-8444-444444444499"), "kind": "customer", "name": "Quartz Analytics"},
                    ],
                },
            ],
        },
    ],
}

ORG_USER_MAP: dict[str, Any] = {
    "admin@cloudpartnerops.example.com": PLATFORM_ID,
    "dist@northwind-distribution.example.com": ORG_TREE["children"][0]["id"],
    "msp@northwind-msp.example.com": ORG_TREE["children"][0]["children"][0]["id"],
    "msp@cascade-it.example.com": ORG_TREE["children"][0]["children"][1]["id"],
    "finops@northwind-msp.example.com": ORG_TREE["children"][0]["children"][0]["id"],
    "billing@northwind-msp.example.com": ORG_TREE["children"][0]["children"][0]["id"],
    "admin@acme-cloud.example.com": ORG_TREE["children"][0]["children"][0]["children"][0]["id"],
    "viewer@acme-cloud.example.com": ORG_TREE["children"][0]["children"][0]["children"][0]["id"],
    "auditor@cloudpartnerops.example.com": PLATFORM_ID,
}


async def _walk(session: AsyncSession, node: dict, parent_path: str | None) -> None:
    # root uses "/" prefix; children append to the parent's path
    org_path = f"/{node['id']}/" if parent_path is None else f"{parent_path}{node['id']}/"
    existing = await session.get(Organization, node["id"])
    if existing is None:
        org = Organization(
            id=node["id"], kind=node["kind"], name=node["name"],
            parent_id=None if parent_path is None else uuid.UUID(parent_path.strip("/").split("/")[-1]),
            path=org_path, currency="USD",
        )
        session.add(org)
    for child in node.get("children", []):
        await _walk(session, child, org_path)
    # (root call passes parent_path=None below)


async def seed() -> dict:
    async with SessionLocal() as session:
        await set_bypass_scope(session)
        org_count = (await session.execute(select(func.count(Organization.id)))).scalar_one()
        if org_count:
            return {"skipped": True, "reason": "database already seeded", "organizations": int(org_count)}

        # organizations
        await _walk(session, ORG_TREE, None)
        await session.flush()

        # roles + permissions
        role_objs: dict[str, Role] = {}
        for key, desc in dict.fromkeys(ROLE_PERMISSIONS, "").items():
            r = Role(key=key, description=desc)
            session.add(r)
            role_objs[key] = r
        perm_objs: dict[str, Permission] = {}
        for key, desc in PERMISSIONS.items():
            p = Permission(key=key, description=desc)
            session.add(p)
            perm_objs[key] = p
        await session.flush()
        for role_key, perms in ROLE_PERMISSIONS.items():
            perm_keys = list(PERMISSIONS.keys()) if "*" in perms else perms
            for pk in perm_keys:
                session.add(RolePermission(role_id=role_objs[role_key].id, permission_id=perm_objs[pk].id))

        # users
        password_hash = hash_password(settings.seed_demo_password)
        for email, display, role_key, org_id in DEMO_USERS:
            org_id = ORG_USER_MAP.get(email, org_id)
            u = User(
                email=email, display_name=display, home_org_id=org_id,
                password_hash=password_hash, status="active",
            )
            session.add(u)
            await session.flush()
            session.add(UserRoleAssignment(user_id=u.id, role_id=role_objs[role_key].id, org_id=org_id))

        # branding: platform root defaults
        await set_org_scope(session, "/")
        session.add(
            BrandingConfig(
                org_id=PLATFORM_ID,
                org_path="/",
                product_name="Cloud PartnerOps",
                primary_color="#0F3D5C",
                accent_color="#C9A227",
                support_email="support@cloudpartnerops.example.com",
                email_sender_name="Cloud PartnerOps",
                terminology={"invoice": "Invoice", "customer": "Customer"},
                feature_flags={"portal_enabled": True, "ai_assistant": True},
            )
        )
        # MSP-level branding (white-label demo)
        msp_org_id = str(ORG_TREE["children"][0]["children"][0]["id"])
        msp_path = f"/{str(PLATFORM_ID)}/{str(ORG_TREE['children'][0]['id'])}/{msp_org_id}/"
        await set_org_scope(session, msp_path)
        session.add(
            BrandingConfig(
                org_id=uuid.UUID(msp_org_id),
                org_path=msp_path,
                product_name="Northwind CloudBill",
                primary_color="#123B2A",
                accent_color="#B8860B",
                support_email="billing@northwind-msp.example.com",
                email_sender_name="Northwind Billing",
                terminology={"invoice": "Statement"},
            )
        )
        await set_bypass_scope(session)
        await record_audit(
            session, None, action="seed.demo_data", org_path="/",
            summary="Demo seed completed (organizations, roles, users, branding)",
            actor_kind="system",
        )
        await session.commit()
        return {"organizations": len(ORG_TREE["children"]) + 1 + 8, "users": len(DEMO_USERS), "roles": len(ROLE_PERMISSIONS)}


def main() -> None:
    if not settings.is_local:
        raise SystemExit("refusing to seed demo data in a non-local environment")
    from app.core.asyncio_util import run_async

    result = run_async(seed())
    print("seed:", result)


if __name__ == "__main__":
    main()
