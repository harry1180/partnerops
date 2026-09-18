"""Domain model package — imports every model module so Alembic autogenerate
and the SQLAlchemy mapper registry see the full schema."""

from app.models.approvals import (  # noqa: F401
    ApprovalRequest,
    Integration,
    NotificationOutbox,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.models.assistant import AIQueryAudit  # noqa: F401
from app.models.audit import AuditEvent  # noqa: F401
from app.models.auth import (  # noqa: F401
    ApiToken,
    Permission,
    Role,
    RolePermission,
    Session,
    User,
    UserRoleAssignment,
)
from app.models.auth import Session as AuthSession  # noqa: F401
from app.models.benefits import (  # noqa: F401
    AllocationPolicy,
    Commitment,
    Credit,
    DiscountProgram,
)
from app.models.billing_core import (  # noqa: F401
    AccountFamily,
    CloudAccount,
    CloudBillingAccount,
    CloudProvider,
    Customer,
    FinopsDimension,
    ResourceGroup,
    Subscription,
)
from app.models.branding import BrandingConfig  # noqa: F401
from app.models.connectors import ProviderConnector  # noqa: F401
from app.models.contracts import (  # noqa: F401
    BillingRule,
    BillingRuleVersion,
    Contract,
    ContractVersion,
    RuleSandboxTest,
)
from app.models.cost import (  # noqa: F401
    CanonicalCostRecord,
    QuarantinedRecord,
    RawBillingFile,
    RawBillingRecord,
)
from app.models.invoices import (  # noqa: F401
    BillingNote,
    Dispute,
    ExportJob,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    PeriodClose,
    ReportSchedule,
)
from app.models.org import Organization  # noqa: F401
from app.models.pricing import (  # noqa: F401
    PricingRun,
    PricingRunItem,
    PricingRunRuleSnapshot,
)
from app.models.reconciliation import (  # noqa: F401
    ProviderBillTotal,
    ReconciliationException,
    ReconciliationRun,
)
