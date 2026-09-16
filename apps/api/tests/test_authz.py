"""Authorization model unit tests."""

from app.services.authz import (
    PERMISSIONS,
    ROLE_PERMISSIONS,
    RequestPrincipal,
    expand_role_permissions,
)


def _principal(roles: list[str], platform: bool = False) -> RequestPrincipal:
    perms = expand_role_permissions(set(roles))
    return RequestPrincipal(
        user_id="u", email="e@x", org_id="o", org_path="/x/", org_kind="reseller",
        roles=frozenset(roles), permissions=frozenset(perms), scope_prefixes=("/x/",),
        is_platform_admin=platform,
    )


def test_catalog_consistency():
    assert set(ROLE_PERMISSIONS["customer_admin"]) <= set(PERMISSIONS.keys())
    assert "*" in ROLE_PERMISSIONS["platform_admin"]


def test_customer_roles_cannot_see_margin_or_internal():
    for role in ("customer_admin", "customer_readonly"):
        perms = expand_role_permissions({role})
        assert "margin.view" not in perms
        assert "internal_notes.view" not in perms
        assert "partner_data.view" not in perms


def test_analyst_roles_are_not_writers_of_money_rules():
    fin = expand_role_permissions({"finops_analyst"})
    assert "invoice.issue" not in fin and "rule.publish" not in fin and "pricing.run" not in fin
    billing = expand_role_permissions({"billing_analyst"})
    assert "pricing.run" in billing and "invoice.issue" not in billing  # issue requires higher role
    assert "recon.waive" not in billing  # waiver = separate authority


def test_auditor_read_only():
    auditor = expand_role_permissions({"auditor"})
    write_perms = {p for p in auditor if p.endswith((".write", ".issue", ".publish", ".manage", ".run", ".approve", ".resolve", ".waive"))}
    assert write_perms == set()
    assert "audit.read" in auditor and "margin.view" in auditor


def test_platform_admin_bypass():
    p = _principal(["auditor"], platform=True)
    assert p.can("invoice.issue")
    p2 = _principal(["customer_readonly"])
    assert not p2.can("margin.view")
    assert p2.can("cost.read")


def test_msp_admin_can_approve_and_issue():
    msp = expand_role_permissions({"msp_admin"})
    assert {"invoice.issue", "rule.approve", "contract.approve", "margin.view"} <= msp
