"""Real-PostgreSQL tenant isolation tests (RLS enforcement).

Env (from repo root .env when the docker stack is up):
  CPO_TEST_PG_URL        = app role URL  (partnerops — subject to RLS)
  CPO_TEST_PG_OWNER_URL  = owner URL     (partnerops_migrate — bypasses RLS
                                           for setup/cleanup only)
Skipped when unset so sqlite-only environments stay green.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

PG_URL = os.environ.get("CPO_TEST_PG_URL", "")
PG_OWNER_URL = os.environ.get("CPO_TEST_PG_OWNER_URL", "")

pytestmark = pytest.mark.skipif(
    not (PG_URL.startswith("postgresql") and PG_OWNER_URL.startswith("postgresql")),
    reason="Set CPO_TEST_PG_URL and CPO_TEST_PG_OWNER_URL to run RLS tests",
)

ROOT = "/11111111-1111-4111-8111-111111111111/"
ORG_A = uuid.uuid4()  # rival tenant
ORG_B = uuid.uuid4()  # tenant under test
PATH_A = f"{ROOT}{ORG_A}/"
PATH_B = f"{ROOT}{ORG_B}/"


@pytest.fixture(scope="module")
def event_loop_policy():
    # psycopg3 async requires a selector loop on Windows
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined]
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="module")
async def engines():
    app_engine = create_async_engine(PG_URL)
    owner_engine = create_async_engine(PG_OWNER_URL)
    yield app_engine, owner_engine
    await app_engine.dispose()
    await owner_engine.dispose()


@pytest_asyncio.fixture
async def app_session(engines) -> AsyncIterator[AsyncSession]:
    app_engine, _ = engines
    async with async_sessionmaker(app_engine, expire_on_commit=False)() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def owner(engines) -> AsyncIterator[AsyncSession]:
    """Owner-role session: seeds tenant orgs, cleans probe rows after test."""
    _, owner_engine = engines
    maker = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with maker() as session:
        for oid, path, name in ((ORG_A, PATH_A, "RLS Org A"), (ORG_B, PATH_B, "RLS Org B")):
            await session.execute(
                text("INSERT INTO organizations (id, kind, name, path, currency, status, created_at, updated_at) "
                     "VALUES (:i,'reseller',:n,:p,'USD','active',now(),now()) ON CONFLICT (id) DO NOTHING"),
                {"i": str(oid), "n": name, "p": path},
            )
        await session.commit()
        yield session
        await session.execute(text("DELETE FROM customers WHERE display_name LIKE 'Probe%'"))
        await session.execute(text("DELETE FROM organizations WHERE name = 'Probe Child'"))
        await session.execute(text("SELECT set_config('app.audit_maintenance', 'on', TRUE)"))
        await session.execute(text("DELETE FROM audit_events WHERE actor_label = 'rls-probe'"))
        await session.commit()


async def scope(session: AsyncSession, path: str) -> None:
    await session.execute(text("SELECT set_config('app.current_org_path', :p, TRUE)"), {"p": path})


async def seed_customer(owner: AsyncSession, org_path: str, name: str) -> uuid.UUID:
    cid = uuid.uuid4()
    oid = uuid.UUID(org_path.strip("/").split("/")[-1])
    await owner.execute(
        text("INSERT INTO customers (id, org_id, org_path, code, display_name, portal_enabled, "
             "billing_address, created_at, updated_at) "
             "VALUES (:i, :o, :p, :c, :n, true, '{}'::jsonb, now(), now())"),
        {"i": str(cid), "o": str(oid), "p": org_path, "c": "P" + str(cid)[:5], "n": name},
    )
    await owner.commit()
    return cid


@pytest.mark.asyncio
async def test_unset_guc_denies_everything(app_session, owner):
    await seed_customer(owner, PATH_B, "Probe Unscoped")
    await scope(app_session, "@@deny@@")
    rows = (await app_session.execute(text("SELECT count(*) FROM customers"))).scalar_one()
    assert rows == 0


@pytest.mark.asyncio
async def test_cross_tenant_invisible(app_session, owner):
    await seed_customer(owner, PATH_A, "Probe Rival Corp")
    await seed_customer(owner, PATH_B, "Probe Own LLC")
    await scope(app_session, PATH_B)
    rows = (await app_session.execute(text("SELECT display_name FROM customers"))).scalars().all()
    assert "Probe Own LLC" in rows
    assert "Probe Rival Corp" not in rows


@pytest.mark.asyncio
async def test_cross_tenant_write_with_check_rejected(app_session, owner):
    await scope(app_session, PATH_B)
    with pytest.raises(Exception) as exc:
        await app_session.execute(
            text("INSERT INTO customers (id, org_id, org_path, code, display_name, portal_enabled, "
                 "billing_address, created_at, updated_at) "
                 "VALUES (:i, :o, :p, 'X', 'Probe Sneaky', true, '{}'::jsonb, now(), now())"),
            {"i": str(uuid.uuid4()), "o": str(ORG_A), "p": PATH_A},
        )
    assert "row-level security" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_child_subtree_visible(app_session, owner):
    child_id = uuid.uuid4()
    child_path = f"{PATH_B}{child_id}/"
    await owner.execute(
        text("INSERT INTO organizations (id, kind, name, path, currency, status, created_at, updated_at) "
             "VALUES (:i,'customer','Probe Child',:p,'USD','active',now(),now())"),
        {"i": str(child_id), "p": child_path},
    )
    await owner.commit()
    await seed_customer(owner, child_path, "Probe Child Customer")
    await scope(app_session, PATH_B)
    rows = (await app_session.execute(text("SELECT display_name FROM customers"))).scalars().all()
    assert "Probe Child Customer" in rows


@pytest.mark.asyncio
async def test_audit_events_append_only(app_session, owner):
    await owner.execute(
        text("INSERT INTO audit_events (id, actor_kind, actor_label, org_path, action, summary, detail, created_at, updated_at) "
             "VALUES (:i,'system','rls-probe','/','seed.demo_data','probe','{}'::jsonb,now(),now())"),
        {"i": str(uuid.uuid4())},
    )
    await owner.commit()
    # app role: UPDATE/DELETE revoked outright
    await scope(app_session, "/")
    with pytest.raises(Exception) as exc:
        await app_session.execute(text("UPDATE audit_events SET summary='tampered' WHERE actor_label='rls-probe'"))
    assert "permission" in str(exc.value).lower()
    await app_session.rollback()
    # even the table owner cannot bypass the append-only trigger
    with pytest.raises(Exception) as exc2:
        await owner.execute(text("UPDATE audit_events SET summary='tampered' WHERE actor_label='rls-probe'"))
    assert "append-only" in str(exc2.value).lower()
    await owner.rollback()


@pytest.mark.asyncio
async def test_issued_invoice_immutability(app_session, owner):
    cid = await seed_customer(owner, PATH_B, "Probe Invoiced Co")
    contract_id, cv_id, inv_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await owner.execute(
        text("INSERT INTO contracts (id, org_id, org_path, customer_id, code, name, status, created_at, updated_at) "
             "VALUES (:i,:o,:p,:c,'C-RLS','RLS Contract','active',now(),now())"),
        {"i": str(contract_id), "o": str(ORG_B), "p": PATH_B, "c": str(cid)},
    )
    await owner.execute(
        text("INSERT INTO contract_versions (id, org_id, org_path, contract_id, version_number, status, "
             "effective_start, currency, billing_cadence, payment_terms, pricing_basis, discount_policy, "
             "credit_sharing_policy, commitment_sharing_policy, support_fee_policy, tax_behavior, "
             "managed_service_fee, invoice_grouping, rounding_rule, approval_required, custom_rates, "
             "rule_bindings, created_at, updated_at) "
             "VALUES (:i,:o,:p,:c,1,'active',now(),'USD','monthly','Net 30','provider_billed','{}'::jsonb,"
             "'{}'::jsonb,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb,'[]'::jsonb,"
             "'{\"mode\":\"half_up\",\"level\":\"line\",\"increment\":\"0.01\"}'::jsonb,false,'[]'::jsonb,"
             "'[]'::jsonb,now(),now())"),
        {"i": str(cv_id), "o": str(ORG_B), "p": PATH_B, "c": str(contract_id)},
    )
    await owner.execute(
        text("INSERT INTO invoices (id, org_id, org_path, invoice_number, customer_id, contract_version_id, "
             "period_start, period_end, currency, status, subtotal, discounts_total, credits_total, fees_total, "
             "adjustments_total, taxes_total, prior_period_adjustments_total, total, provider_cost_total, "
             "margin_total, payment_terms, grouping, branding_snapshot, created_at, updated_at) "
             "VALUES (:i,:o,:p,'INV-RLS-1',:c,:cv,now(),now(),'USD','issued',100,0,0,0,0,0,0,100,80,20,"
             "'Net 30','[]'::jsonb,'{}'::jsonb,now(),now())"),
        {"i": str(inv_id), "o": str(ORG_B), "p": PATH_B, "c": str(cid), "cv": str(cv_id)},
    )
    await owner.commit()
    try:
        await scope(app_session, PATH_B)
        with pytest.raises(Exception) as exc:
            await app_session.execute(
                text("UPDATE invoices SET total = 999999 WHERE id = :i"), {"i": str(inv_id)}
            )
        assert "immutable" in str(exc.value).lower()
        await app_session.rollback()
        await scope(app_session, PATH_B)  # fresh transaction after rollback
        with pytest.raises(Exception) as exc2:
            await app_session.execute(
                text("UPDATE invoices SET status = 'draft' WHERE id = :i"), {"i": str(inv_id)}
            )
        assert "immutable" in str(exc2.value).lower() or "transition" in str(exc2.value).lower()
    finally:
        await app_session.rollback()
        await owner.execute(text("DELETE FROM invoices WHERE id=:i"), {"i": str(inv_id)})
        await owner.execute(text("DELETE FROM contract_versions WHERE id=:i"), {"i": str(cv_id)})
        await owner.execute(text("DELETE FROM contracts WHERE id=:i"), {"i": str(contract_id)})
        await owner.execute(text("DELETE FROM customers WHERE id=:i"), {"i": str(cid)})
        await owner.commit()
