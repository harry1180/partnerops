# Demo Credentials (LOCAL ONLY)

These accounts exist only when the database is seeded in a local/test
environment (`APP_ENV=local|test`). The seed refuses to run elsewhere, and
`GET /api/v1/auth/demo-accounts` 404s outside local.

- Password for every account: value of `SEED_DEMO_PASSWORD` in `.env`
  (default: `Demo-2026-LocalOnly`)
- Sign in at http://localhost:3000/login (list is also shown there)

| Email | Role | Organization (scope) | What you'll see |
|-------|------|----------------------|-----------------|
| admin@cloudpartnerops.example.com | platform_admin | Cloud PartnerOps Platform | Everything, all tenants |
| auditor@cloudpartnerops.example.com | auditor | Platform | Full audit trail, read-only incl. margins |
| dist@northwind-distribution.example.com | distributor_admin | Northwind Distribution | Own branch incl. both MSPs |
| msp@northwind-msp.example.com | msp_admin | Northwind MSP | Customers, contracts, billing, invoices, portal admin |
| msp@cascade-it.example.com | msp_admin | Cascade IT Partners | Same, but cannot see Northwind MSP data (tenant-isolation demo) |
| finops@northwind-msp.example.com | finops_analyst | Northwind MSP | Costs/optimization/governance, no money-rule authority |
| billing@northwind-msp.example.com | billing_analyst | Northwind MSP | Pricing runs, invoice drafting, reconciliation (no issue/approve) |
| admin@acme-cloud.example.com | customer_admin | Acme Cloud Co (customer of Northwind MSP) | Portal admin; cannot see partner margin or internal notes |
| viewer@acme-cloud.example.com | customer_readonly | Acme Cloud Co | Portal read-only |

Sister distributor **Southbridge Partners** (reseller: Southbridge MSP,
customer: Quartz Analytics) exists with no users — deliberately, to prove
cross-tenant invisibility in tests and the Phase-1 Playwright journey.

Tenant-isolation quick check (local, seeded): sign in as
`msp@cascade-it.example.com`, open Customers — you will see none of Acme's
records, and probing Acme's customer ID in the URL returns "not found", not
"forbidden".
