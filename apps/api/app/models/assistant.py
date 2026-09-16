"""AI-assistant query audit (charter: AIQueryAudit entity).

Every assistant query is recorded with the asking user, resolved scope,
matched tools, cited entity ids, whether the answer exposed only
customer-safe data, and the refusal reason when applicable. Answers from the
deterministic demo mode are labeled as such in `mode`.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, TimestampMixin
from app.types import JSONVariant


class AIQueryAudit(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "ai_query_audit"

    asked_by = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="deterministic_demo")
    tools_used: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False, default=list)
    citations: Mapped[list[dict]] = mapped_column(JSONVariant, nullable=False, default=list)
    exposed_customer_safe: Mapped[bool] = mapped_column(default=True, nullable=False)
    refused: Mapped[bool] = mapped_column(default=False, nullable=False)
    refusal_reason: Mapped[str | None] = mapped_column(String(255))
    sensitive: Mapped[bool] = mapped_column(default=False, nullable=False)
    answer_summary: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    latency_ms: Mapped[int | None] = mapped_column()
