# Entity-Relationship Diagram (Phase 0 schema)

Rendered via Mermaid (GitHub/GitLab render natively). 54 tables in the
initial migration; grouped by bounded context. Money columns are
`NUMERIC(20,6)`, ids are UUID, tenant tables carry `org_path` (RLS).

```mermaid
erDiagram
    ORG_TREE["organizations"] {
        uuid id PK
        string kind "platform|distributor|reseller|customer|internal"
        string path UK "materialized /id/id/…"
        uuid parent_id FK
        string currency
        string status
    }

    USER_MODEL["users"] {
        uuid id PK
        string email UK
        text password_hash "argon2id; NULL = SSO-only"
        uuid home_org_id FK
        string status
        bool mfa_enabled
        int failed_login_count
        datetime locked_until
    }
    SESSIONS["sessions"] { uuid id PK; uuid user_id FK; string token_hash UK; string csrf_token; datetime expires_at; datetime revoked_at }
    API_TOKENS["api_tokens"] { uuid id PK; string token_hash UK; uuid org_id FK; json scopes; datetime expires_at; datetime revoked_at }
    ROLES["roles"] { uuid id PK; string key UK; bool is_customer_facing }
    PERMISSIONS["permissions"] { uuid id PK; string key UK }
    ROLE_PERMISSIONS["role_permissions"] { uuid role_id FK; uuid permission_id FK }
    USER_ROLE_ASSIGNMENTS["user_role_assignments"] { uuid user_id FK; uuid role_id FK; uuid org_id FK; json attributes "ABAC-ready" }
    AUDIT_EVENTS["audit_events"] { uuid id PK; string org_path; string action; string entity_type; uuid entity_id; text summary; json detail; string correlation_id "append-only (trigger+grants)" }

    CUSTOMERS["customers"] { uuid id PK; string org_path "RLS"; string code; string display_name; numeric target_margin_pct; json billing_address; datetime deleted_at }
    ACCOUNT_FAMILIES["account_families"] { uuid id PK; uuid customer_id FK; string name; string org_path }
    CLOUD_PROVIDERS["cloud_providers"] { uuid id PK; string code UK "aws|azure|gcp"; string adapter_key; string status }
    CLOUD_BILLING_ACCOUNTS["cloud_billing_accounts"] { uuid id PK; string provider_code; string external_id; string org_path "partner-owned" }
    CLOUD_ACCOUNTS["cloud_accounts"] { uuid id PK; string external_id; string provider_code; uuid billing_account_id FK; uuid account_family_id FK; string allocation_status; string organization_path }
    SUBSCRIPTIONS["subscriptions"] { uuid id PK; uuid cloud_account_id FK; string external_id }
    RESOURCE_GROUPS["resource_groups"] { uuid id PK; uuid subscription_id FK; string name }
    FINOPS_DIMENSIONS["finops_dimensions"] { uuid id PK; string kind "application|environment|owner|cost_center"; string value; uuid customer_id FK }

    CONTRACTS["contracts"] { uuid id PK; uuid customer_id FK; string code; string status }
    CONTRACT_VERSIONS["contract_versions"] { uuid id PK; uuid contract_id FK; int version_number; string status; datetime effective_start; datetime effective_end; string pricing_basis; json policies "discount|credit|commitment|support|tax|msf"; numeric minimum_monthly; json rounding_rule; json rule_bindings "immutable once used" }
    BILLING_RULES["billing_rules"] { uuid id PK; string rule_type; uuid contract_id FK; string status }
    BILLING_RULE_VERSIONS["billing_rule_versions"] { uuid id PK; uuid rule_id FK; int version_number; int priority; int calc_order; json filters; json parameters; bool high_impact; datetime approved_at }
    RULE_SANDBOX_TESTS["rule_sandbox_tests"] { uuid id PK; uuid rule_version_id FK; json result_summary "before-publish evidence" }

    RAW_BILLING_FILES["raw_billing_files"] { uuid id PK; string sha256; int parser_version UK-composite; string object_key; string status; int row_count }
    RAW_BILLING_RECORDS["raw_billing_records"] { uuid id PK; uuid file_id FK; int row_number; string dedupe_key; json payload "verbatim source" }
    QUARANTINED_RECORDS["quarantined_records"] { uuid id PK; string reason; json payload; string status }
    CANONICAL_COST_RECORDS["canonical_cost_records"] { uuid id PK; string source_record_id; datetime billing_period_start "partition key"; uuid cloud_account_id FK; uuid customer_id FK; string service; string sku; numeric quantity; numeric provider_billed; numeric amortized; numeric credit; json tags; json source_metadata "lineage to file+row" }

    COMMITMENTS["commitments"] { uuid id PK; string kind "RI|SP|EA|PPA|volume"; uuid customer_id FK; numeric hourly_commitment }
    CREDITS["credits"] { uuid id PK; string kind; numeric amount_total; numeric amount_used; string allocation_status }
    DISCOUNT_PROGRAMS["discount_programs"] { uuid id PK; json rates; datetime effective_start }
    ALLOCATION_POLICIES["allocation_policies"] { uuid id PK; string benefit_class; string mode "pass_full|…|custom"; numeric share_pct }

    PRICING_RUNS["pricing_runs"] { uuid id PK; uuid customer_id FK; uuid contract_version_id FK; int run_number; uuid supersedes_id FK; string engine_version; json totals; string approval_status }
    PRICING_RUN_ITEMS["pricing_run_items"] { uuid id PK; uuid run_id FK; int item_number; json source_record_ids; json rule_version_ids; numeric input_amount; numeric output_amount; text formula; json calculation_trace "full lineage" }
    PRICING_RUN_RULE_SNAPSHOTS["pricing_run_rule_snapshots"] { uuid run_id FK; uuid rule_version_id FK; json parameters "frozen parameters" }

    INVOICES["invoices"] { uuid id PK; string invoice_number UK-per-org; uuid customer_id FK; uuid contract_version_id FK; uuid pricing_run_id FK; string status "10-state lifecycle"; numeric total; numeric provider_cost_total "internal"; numeric margin_total "internal"; json branding_snapshot "trigger-guarded immutability once issued" }
    INVOICE_LINES["invoice_lines"] { uuid id PK; uuid invoice_id FK; int line_number; uuid pricing_run_item_id FK; numeric amount; json source_record_ids; bool customer_visible }
    BILLING_NOTES["billing_notes"] { uuid id PK; string kind "credit|debit"; uuid invoice_id FK; numeric amount }
    DISPUTES["disputes"] { uuid id PK; uuid invoice_id FK; string status; text resolution_notes }
    EXPORT_JOBS["export_jobs"] { uuid id PK; string kind; string object_key; string sha256 }
    PERIOD_CLOSES["period_closes"] { uuid id PK; datetime billing_period_start UK; string status }
    INVOICE_SEQUENCES["invoice_sequences"] { string org_path; string pattern_key; int next_value "configurable numbering" }

    PROVIDER_BILL_TOTALS["provider_bill_totals"] { uuid id PK; string billing_account_ref; numeric billed_total "leg 1 of reconciliation" }
    RECONCILIATION_RUNS["reconciliation_runs"] { uuid id PK; numeric tolerance_abs; numeric tolerance_pct; json summary }
    RECONCILIATION_EXCEPTIONS["reconciliation_exceptions"] { uuid id PK; string exc_type; string materiality; numeric amount_delta; text explanation; string status "open|investigating|resolved|waived"; uuid waiver_approval_id FK }

    APPROVALS["approvals"] { uuid id PK; string kind "rule_publish|recon_waiver|…"; uuid maker_id FK; uuid checker_id FK "maker != checker"; numeric impact_amount; string status }
    BRANDING_CONFIGS["branding_configs"] { uuid id PK; string org_path; string product_name; string logo_object_key; string primary_color; json terminology; json feature_flags }
    INTEGRATIONS["integrations"] { uuid id PK; string kind; json config; json secret_refs "no plaintext" }
    WEBHOOK_ENDPOINTS["webhook_endpoints"] { uuid id PK; string url; json events; string secret_ref }
    WEBHOOK_DELIVERIES["webhook_deliveries"] { uuid endpoint_id FK; json payload; int attempts }
    NOTIFICATION_OUTBOX["notification_outbox"] { uuid id PK; string channel; string recipient; string status }
    AI_QUERY_AUDIT["ai_query_audit"] { uuid id PK; uuid asked_by FK; text question; json citations; bool refused; bool sensitive }

    ORG_TREE ||--o{ ORG_TREE : "parent → children"
    ORG_TREE ||--o{ USER_MODEL : "home org"
    USER_MODEL ||--o{ SESSIONS : ""
    USER_MODEL ||--o{ USER_ROLE_ASSIGNMENTS : ""
    ROLES ||--o{ USER_ROLE_ASSIGNMENTS : ""
    ROLES ||--o{ ROLE_PERMISSIONS : ""
    PERMISSIONS ||--o{ ROLE_PERMISSIONS : ""
    ORG_TREE ||--o{ AUDIT_EVENTS : "org_path"
    ORG_TREE ||--o{ CUSTOMERS : "org_path"
    CUSTOMERS ||--o{ ACCOUNT_FAMILIES : ""
    ACCOUNT_FAMILIES ||--o{ CLOUD_ACCOUNTS : ""
    CLOUD_BILLING_ACCOUNTS ||--o{ CLOUD_ACCOUNTS : "payer → linked"
    CLOUD_ACCOUNTS ||--o{ SUBSCRIPTIONS : ""
    SUBSCRIPTIONS ||--o{ RESOURCE_GROUPS : ""
    CUSTOMERS ||--o{ FINOPS_DIMENSIONS : ""
    CUSTOMERS ||--o{ CONTRACTS : ""
    CONTRACTS ||--o{ CONTRACT_VERSIONS : "versioned"
    CONTRACT_VERSIONS ||--o{ BILLING_RULES : "rule_bindings"
    BILLING_RULES ||--o{ BILLING_RULE_VERSIONS : "versioned"
    BILLING_RULE_VERSIONS ||--o{ RULE_SANDBOX_TESTS : ""
    RAW_BILLING_FILES ||--o{ RAW_BILLING_RECORDS : ""
    RAW_BILLING_RECORDS ||--o| CANONICAL_COST_RECORDS : "normalized"
    RAW_BILLING_FILES ||--o{ QUARANTINED_RECORDS : ""
    CLOUD_ACCOUNTS ||--o{ CANONICAL_COST_RECORDS : ""
    CUSTOMERS ||--o{ CANONICAL_COST_RECORDS : ""
    CONTRACT_VERSIONS ||--o{ PRICING_RUNS : "pinned"
    PRICING_RUNS ||--o{ PRICING_RUN_ITEMS : ""
    PRICING_RUNS ||--o{ PRICING_RUN_RULE_SNAPSHOTS : ""
    CANONICAL_COST_RECORDS ||--o{ PRICING_RUN_ITEMS : "source_record_ids"
    PRICING_RUNS ||--o{ INVOICES : ""
    INVOICES ||--o{ INVOICE_LINES : ""
    PRICING_RUN_ITEMS ||--o{ INVOICE_LINES : "lineage"
    INVOICES ||--o{ BILLING_NOTES : "corrections"
    INVOICES ||--o{ DISPUTES : ""
    INVOICES ||--o{ EXPORT_JOBS : ""
    PROVIDER_BILL_TOTALS ||--o{ RECONCILIATION_EXCEPTIONS : "3-way"
    RECONCILIATION_RUNS ||--o{ RECONCILIATION_EXCEPTIONS : ""
    APPROVALS ||--o{ RECONCILIATION_EXCEPTIONS : "waivers"
    ORG_TREE ||--o{ BRANDING_CONFIGS : "walk-up"
    ORG_TREE ||--o{ INTEGRATIONS : ""
    ORG_TREE ||--o{ COMMITMENTS : ""
    ORG_TREE ||--o{ CREDITS : ""
    CUSTOMERS ||--o{ CREDITS : ""
    ORG_TREE ||--o{ ALLOCATION_POLICIES : ""
    USER_MODEL ||--o{ AI_QUERY_AUDIT : ""
```

## Phase 3 additions (shipped)

`provider_connectors` — org_path-scoped connector config (provider, billing
account ref, cadence, last-ingest evidence, FK to the last ingested
`raw_billing_files` row). `provider_bill_totals` gained `level`
(invoice|account) for per-cloud-account reconciliation grain.
`subscriptions`/`resource_groups` (modeled since Phase 0) are now populated
by the Azure demo seed.

## Phase 4 additions (shipped)

`budgets`, `cost_anomalies`, `recommendations`, `governance_policies`,
`governance_findings`, `policy_exceptions` — all org_path-scoped with the
same RLS subtree policy; anomaly/recommendation/finding rows carry evidence
JSON and dedupe keys for idempotent re-runs.

## Phase 5 additions (shipped) & remaining

`ai_query_audit`, `webhook_endpoints`, `webhook_deliveries`,
`notification_outbox`, `integrations`, `export_jobs` — all live since
Phase 0 schemas, all now exercised end-to-end (assistant answers + signed
webhook deliveries + ERP `cpo.erp.v1` exports, Phase 5). Remaining
planned: provider-config governance connectors, marketplace invoice-sync
tables (Phase 6+).
