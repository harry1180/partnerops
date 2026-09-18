/**
 * Cloud PartnerOps — critical end-to-end journey (charter list).
 *
 *  1. Create customer and account family         (UI)
 *  2. Import synthetic AWS billing data          (UI)
 *  3. Map the discovered unmapped account        (UI allocation worklist)
 *  4. Create a contract                          (UI)
 *  5. Create billing rule + sandbox preview      (UI)
 *  6. Bind rule, activate contract               (UI)
 *  7. Run pricing, create invoice                (UI)
 *  8. Drive invoice lifecycle to issued          (UI)
 *  9. Reconcile against the provider bill        (UI)
 * 10. Margins dashboard shows the customer       (UI)
 * 11. Customer portal: see invoice, file dispute (UI, provisioned user)
 * 12. Assistant question about invoice change    (Phase 5 — honestly skipped)
 * 13. Another tenant cannot access the data      (API 404 + auditor evidence)
 *
 * Requires: API on :8001 (docker postgres seeded), web on :3000.
 * Re-runnable: entities carry a unique run tag.
 */

import { expect, test, type Page } from "@playwright/test";
import { apiGet, apiLogin, apiPost, runTag, uiLogin, WEB, type ApiCtx } from "./helpers";

const TAG = runTag();
const CODE = `E2E${TAG}`.toUpperCase().slice(0, 16);
const NAME = `Testco E2E ${TAG}`;
const RULE_CODE = `E2E-${TAG}`;
const CONTRACT_CODE = `E2E-${TAG}`;
const FAMILY_NAME = `E2E Production ${TAG}`;
const UNMAPPED = "999999999999";
const AZURE_ORPHAN = "unmapped-0000-4000-8000-000000000099";  // Phase 3 fixture

let msp: ApiCtx;
let customerId = "";
let invoiceNumber = "";
let invoiceId = "";

const re = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Field labels contain a required "*" span; label text is `Name*`. */
function byLabel(scope: Page | import("@playwright/test").Locator, label: string) {
  return scope.getByLabel(new RegExp(`^${re(label)}\\s*\\*?$`));
}

test.beforeAll(async () => {
  msp = await apiLogin("msp@northwind-msp.example.com");
});
test.afterAll(async () => {
  await msp.ctx.dispose();
});

test.describe("partner billing journey", () => {
  test("1. MSP creates customer and account family in the UI", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/customers`);
    await page.getByRole("button", { name: "Add customer" }).click();
    await byLabel(page, "Customer name").fill(NAME);
    await byLabel(page, "Code").fill(CODE);
    await page.getByRole("button", { name: "Create customer" }).click();

    await page.getByRole("button", { name: /^Search$/ }).click();
    const search = page.getByLabel(/search customers/i);
    await expect(search).toBeVisible({ timeout: 15_000 });
    await search.fill(TAG);
    await page.getByRole("button", { name: /^Search$/ }).click();

    const link = page.locator("a", { hasText: NAME }).first();
    await expect(link).toBeVisible({ timeout: 15_000 });
    const href = await link.getAttribute("href");
    expect(href).toMatch(/^\/customers\//);
    customerId = (href as string).split("/").pop() as string;

    await page.goto(`${WEB}${href}`);
    await page.getByRole("button", { name: "Add family" }).click();
    await byLabel(page, "Name").fill(FAMILY_NAME);
    await page.getByRole("button", { name: "Create family" }).click();
    await expect(page.getByText(FAMILY_NAME)).toBeVisible({ timeout: 15_000 });
  });

  test("2. Import synthetic AWS billing via Cloud Accounts page", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/cloud-accounts`);
    await page.getByRole("button", { name: "Import synthetic AWS data" }).click();
    await expect(page.getByText(/cost records across 3 files/)).toBeVisible({ timeout: 90_000 });
    const row = page.locator("tr", { hasText: UNMAPPED }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    // unmapped on first run; mapped is also acceptable on re-runs (idempotent demo)
    await expect(row.getByText(/unmapped|mapped/i).first()).toBeVisible();
  });

  test("2b. (Phase 3) Import synthetic Azure billing; orphan subscription surfaces; connectors panel is honest", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/cloud-accounts`);
    await page.getByRole("button", { name: "Import synthetic Azure data" }).click();
    await expect(page.getByText(/AZURE: \d+ cost records across 3 files/)).toBeVisible({ timeout: 90_000 });
    const orphan = page.locator("tr", { hasText: AZURE_ORPHAN }).first();
    await expect(orphan).toBeVisible({ timeout: 20_000 });
    await expect(orphan.getByText("AZURE").first()).toBeVisible();
    // connectors panel: seeded, synthetic-mode, no fake live pull
    await expect(page.getByText("Provider connectors")).toBeVisible({ timeout: 15_000 });
    const azConn = page.locator("tr", { hasText: "Northwind Azure Cost Export" }).first();
    await expect(azConn).toBeVisible();
    await expect(azConn.getByText("no live pull")).toBeVisible();
  });

  test("3. Map the discovered account to the new family", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/cloud-accounts`);
    const row = page.locator("tr", { hasText: UNMAPPED }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole("button", { name: /map/i }).first().click();
    const modal = page.getByRole("dialog");
    const sel = modal.locator("select").first();
    // families load async — wait for our option before reading it
    const opt = sel.locator("option", { hasText: FAMILY_NAME }).first();
    await expect(opt).toHaveCount(1, { timeout: 20_000 });
    await sel.selectOption(await opt.getAttribute("value"));
    await modal.getByRole("button", { name: "Map", exact: true }).click();
    const mapped = page.locator("tr", { hasText: UNMAPPED }).first();
    await expect(mapped.getByText("mapped", { exact: true })).toBeVisible({ timeout: 15_000 });
  });

  test("4. Create contract (draft) in the UI", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/contracts`);
    await page.getByRole("button", { name: "New contract" }).click();
    await byLabel(page, "Customer").selectOption({ label: `${NAME} (${CODE})` });
    await byLabel(page, "Code").fill(CONTRACT_CODE);
    await byLabel(page, "Name").fill(`E2E Contract ${TAG}`);
    await byLabel(page, "Effective from").fill("2026-06-01");
    await byLabel(page, "Minimum monthly (optional)").fill("1500.00");
    await page.getByRole("button", { name: "Create v1 (draft)" }).click();
    await expect(page.getByText(CONTRACT_CODE)).toBeVisible({ timeout: 15_000 });
  });

  test("5. Create markup rule, preview it in the sandbox, publish", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/billing-rules`);
    await page.getByRole("button", { name: "New rule" }).click();
    await byLabel(page, "Code").fill(RULE_CODE);
    await byLabel(page, "Name").fill("E2E 12% markup");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText(RULE_CODE)).toBeVisible({ timeout: 15_000 });

    // sandbox preview on June history (draft v2 row)
    await page.locator(`.cpo-card:has-text("${RULE_CODE}")`)
      .getByRole("button", { name: "Test rule" }).last().click();
    const modal = page.getByRole("dialog");
    await modal.getByRole("button", { name: "Preview on history" }).click();
    await expect(modal.getByText(/Baseline/i)).toBeVisible({ timeout: 60_000 });
    await expect(modal.getByText(/read-only/)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden({ timeout: 10_000 });

    // publish v2
    await page.locator(`.cpo-card:has-text("${RULE_CODE}")`)
      .locator("button", { hasText: "Publish" }).last().click();
    await expect(page.getByText(/published/i).first()).toBeVisible({ timeout: 15_000 });
  });

  test("6. Bind rule to contract and activate it", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/contracts`);
    const card = page.locator(`.cpo-card:has-text("${CONTRACT_CODE}")`).last();
    await card.getByRole("button", { name: "Bind rules" }).click();
    const modal = page.getByRole("dialog");
    await modal.locator(`label:has-text("${RULE_CODE}")`).click();
    await modal.getByRole("button", { name: "Save bindings" }).click();
    await expect(page.getByRole("dialog")).toBeHidden({ timeout: 15_000 });

    const card2 = page.locator(`.cpo-card:has-text("${CONTRACT_CODE}")`).last();
    await card2.getByRole("button", { name: "Activate" }).click();
    await expect(card2.getByText(/active/i).first()).toBeVisible({ timeout: 15_000 });
  });

  test("7. Run pricing and create the invoice", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/pricing`);
    await byLabel(page, "Customer").selectOption({ label: NAME });
    await byLabel(page, "Contract version").waitFor();
    await byLabel(page, "Billing period").selectOption("0"); // June 2026
    await page.getByRole("button", { name: "Run pricing" }).click();
    await expect(page.getByText(/Lineage —/)).toBeVisible({ timeout: 90_000 });

    await page.getByRole("button", { name: "Create invoice from this run" }).click();
    await page.waitForURL(/\/invoices\/[0-9a-f-]+/, { timeout: 20_000 });
    invoiceId = new URL(page.url()).pathname.split("/").pop() as string;
    const heading = page.locator("h1").filter({ hasText: /-2026\d\d-\d{4}/ }).first();
    await expect(heading).toBeVisible({ timeout: 15_000 });
    invoiceNumber = (await heading.textContent())?.trim() ?? "";
    expect(invoiceNumber).toMatch(/-2026\d\d-\d{4}$/);
  });

  test("8. Drive invoice through lifecycle to issued", async ({ page }) => {
    test.skip(!invoiceId, "invoice step failed");
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/invoices/${invoiceId}`);
    await page.getByRole("button", { name: "Send to review" }).click();
    await expect(page.getByText(/under review/i).first()).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: /^Approve$/ }).click();
    await expect(page.getByText(/^approved$/i).first()).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Issue invoice" }).click();
    await expect(page.getByText(/^issued$/i).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("link", { name: /Download PDF/ })).toBeVisible();
  });

  test("9. Reconcile August against the provider bill", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/reconciliation`);
    await byLabel(page, "Period to reconcile").selectOption("2"); // August
    await page.getByRole("button", { name: "Run reconciliation" }).click();
    await expect(page.getByText(/exceptions \(\d+ material\)/)).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("Data quality")).toBeVisible();
  });

  test("10. Margins page shows the customer", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/margins`);
    await expect(page.getByText(NAME)).toBeVisible({ timeout: 30_000 });
  });

  test("10b. (Phase 4) Budgets page: seeded cap, anomaly pass finds the planted spike; governance evaluates", async ({ page }) => {
    await uiLogin(page, "msp@northwind-msp.example.com");
    await page.goto(`${WEB}/budgets`);
    await expect(page.getByText("Cobalt August cap")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Run anomaly pass" }).click();
    await expect(page.getByText(/Anomaly pass: \d+ new.*subject/)).toBeVisible({ timeout: 60_000 });
    // the anomalies card renders (service-specific rows depend on prior reviews)
    await expect(page.getByText("Open anomalies")).toBeVisible({ timeout: 15_000 });

    await page.goto(`${WEB}/governance`);
    await page.getByRole("button", { name: "Evaluate now" }).click();
    await expect(page.getByText(/Evaluated: \d+ new findings/)).toBeVisible({ timeout: 60_000 });
    // every finding row carries the evidence dialog
    const row = page.locator("table").last().locator("tbody tr").first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole("button").first().click();
    await expect(page.getByText("Evidence", { exact: true })).toBeVisible({ timeout: 10_000 });
  });

  test("11. Customer user sees the issued invoice in the portal and disputes it", async ({ browser }) => {
    test.skip(!invoiceId, "invoice step failed");
    const cust = await apiGet<{ id: string; org_path: string }>(msp, `/api/v1/customers/${customerId}`);
    const custOrgId = cust.org_path.split("/").filter(Boolean).pop() as string; // customer org node
    const email = `e2e-${TAG.toLowerCase()}@testco.example.com`;
    const userPw = "E2e-Demo-Pass-2026!";
    try {
      await apiPost(msp, "/api/v1/users", {
        email, display_name: "Erin Testco", org_id: custOrgId,
        roles: ["customer_admin"], password: userPw,
      });
    } catch (e) {
      expect(String(e)).toContain("email_exists");
    }

    const ctx = await browser.newContext({ baseURL: WEB });
    const page = await ctx.newPage();
    await uiLogin(page, email, userPw);
    await page.goto(`${WEB}/portal/invoices`);
    await expect(page.getByText(invoiceNumber)).toBeVisible({ timeout: 20_000 });

    // portal must not leak partner economics into RENDERED content.
    // (body textContent would include Next dev-mode RSC <script> payloads —
    // assert on the visible <main> region instead.)
    await page.goto(`${WEB}/portal/usage`);
    const rendered = (await page.locator("main").innerText()) ?? "";
    expect(rendered).not.toMatch(/margin/i);
    expect(rendered).not.toMatch(/provider cost/i);
    // and at the API boundary: the portal payload itself (via the app proxy)
    const api = await (await page.request.get("/api-backend/api/v1/portal/usage/summary")).json();
    expect(JSON.stringify(api)).not.toMatch(/margin|provider_cost/i);

    // dispute
    await page.goto(`${WEB}/portal/invoices`);
    await page.getByRole("button", { name: "Dispute" }).first().click();
    const modal = page.getByRole("dialog");
    await byLabel(modal, "Subject").fill("E2E test dispute");
    await byLabel(modal, "What looks wrong?").fill("Checking the portal dispute path end to end.");
    await modal.getByRole("button", { name: "File dispute" }).click();
    await expect(modal.getByText(/DSP-/)).toBeVisible({ timeout: 20_000 });
    await ctx.close();

    const disputes = await apiGet<{ items: { subject: string }[] }>(msp, "/api/v1/disputes");
    expect(disputes.items.some((d) => d.subject === "E2E test dispute")).toBeTruthy();
  });

  test("12. Assistant question about invoice change — Phase 5", () => {
    test.skip(true, "AI PartnerOps assistant ships in Phase 5; no fake answer is wired here.");
  });
});

test.describe("tenant isolation", () => {
  test("13a. other tenant cannot see the E2E customer or invoice", async () => {
    test.skip(!customerId || !invoiceId, "journey steps required first");
    const other = await apiLogin("msp@cascade-it.example.com");
    try {
      const list = await other.ctx.get(`/api/v1/customers?search=${TAG}`);
      expect(list.status()).toBe(200);
      expect(((await list.json()).items as unknown[]).length).toBe(0);
      const inv = await other.ctx.get(`/api/v1/invoices/${invoiceId}`);
      expect(inv.status()).toBe(404); // never 403 — existence is not leaked
      const mine = await msp.ctx.get(`/api/v1/invoices/${invoiceId}`);
      expect(mine.status()).toBe(200); // sanity: it does exist
    } finally {
      await other.ctx.dispose();
    }
  });

  test("13b. auditor finds the journey's audit events", async () => {
    test.skip(!invoiceId, "journey steps required first");
    const aud = await apiLogin("auditor@cloudpartnerops.example.com");
    try {
      const r = await aud.ctx.get("/api/v1/audit-events?page=1&page_size=200");
      expect(r.status()).toBe(200);
      const acts = new Set(((await r.json()).items as { action: string }[]).map((e) => e.action));
      expect(acts.has("cloud_account.mapped")).toBeTruthy();
      expect(acts.has("invoice.state_changed")).toBeTruthy();
    } finally {
      await aud.ctx.dispose();
    }
  });
});
