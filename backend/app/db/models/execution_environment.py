"""Immutable, content-addressed execution environment closure for calculations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.software import SoftwareRelease
    from app.db.models.workflow import WorkflowToolRelease


class ExecutionEnvironmentManifest(Base, TimestampMixin):
    """A normalized, immutable manifest whose SHA-256 identifies its exact closure.

    The calculation relation is intentionally many-to-one: identical closed
    environments deduplicate, but the database trigger forbids changing a
    shared row after it has been recorded.
    """

    __tablename__ = "execution_environment_manifest"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    runtime_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    runtime_locator: Mapped[str] = mapped_column(Text, nullable=False)
    executable_locator: Mapped[str] = mapped_column(Text, nullable=False)
    software_release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("software_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    workflow_tool_release_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("workflow_tool_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    closure_json: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    canonical_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    calculations: Mapped[list["Calculation"]] = relationship(back_populates="execution_environment_manifest")
    software_release: Mapped["SoftwareRelease"] = relationship()
    workflow_tool_release: Mapped["WorkflowToolRelease | None"] = relationship()

    __table_args__ = (
        CheckConstraint("content_digest ~ '^sha256:[0-9a-f]{64}$'", name="content_digest_sha256"),
        CheckConstraint("jsonb_typeof(closure_json) = 'array'", name="closure_json_array"),
        CheckConstraint("jsonb_typeof(canonical_json) = 'object'", name="canonical_json_object"),
        UniqueConstraint("content_digest", name="uq_execution_environment_manifest_content_digest"),
    )
