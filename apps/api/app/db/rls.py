"""Row-level security helpers.

Every tenant-scoped table has `org_path` (materialized path of organization
ids, e.g. "/root/dis1/res2/cust3/") and RLS policies that compare against the
session GUC `app.current_org_path`. A user scoped to an org can read rows in
its subtree; org_path is the ancestor test (LIKE prefix%, index-friendly).

The application NEVER leaves the GUC unset when touching tenant-scoped tables:
an unset GUC means "deny all" under the policy. On non-PostgreSQL dialects
(local sqlite test runs) these are no-ops; API-layer scope tests still apply.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# A value that can never prefix a real org path ("/" + uuid segments):
# unset scope therefore means deny-all under the RLS policies.
DENY_PREFIX = "@@deny@@"


def _is_postgres(session: AsyncSession) -> bool:
    try:
        return session.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


async def set_org_scope(session: AsyncSession, org_path_prefix: str | None) -> None:
    """Bind the connection's RLS scope to a subtree.

    Passing None (or an empty prefix) sets the deny-all sentinel — it can
    never be a prefix of a real org_path, so policies match nothing.
    """
    if not _is_postgres(session):
        return
    value = org_path_prefix if org_path_prefix else DENY_PREFIX
    await session.execute(
        text("SELECT set_config('app.current_org_path', :p, TRUE)"),
        {"p": value},
    )


async def set_bypass_scope(session: AsyncSession) -> None:
    """Set the platform-scope prefix ('/' = root subtree). The API layer must
    only call this after verifying the caller holds platform_admin."""
    if not _is_postgres(session):
        return
    await session.execute(text("SELECT set_config('app.current_org_path', '/', TRUE)"))
