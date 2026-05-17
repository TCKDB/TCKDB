from datetime import date
from typing import TYPE_CHECKING, Self

from pydantic import Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import FrequencyScaleKind
from tckdb_schemas.utils import normalize_optional_text, normalize_required_text

if TYPE_CHECKING:
    from tckdb_schemas.literature import LiteratureUploadRequest


class SoftwareReleaseRef(SchemaBase):
    """Upload-facing reference to a software release."""

    name: str = Field(min_length=1)
    version: str | None = None
    revision: str | None = None
    build: str | None = None
    release_date: date | None = None
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_required_text(value)

    @model_validator(mode="after")
    def normalize_optional_fields(self) -> Self:
        self.version = normalize_optional_text(self.version)
        self.revision = normalize_optional_text(self.revision)
        self.build = normalize_optional_text(self.build)
        self.notes = normalize_optional_text(self.notes)
        return self


class WorkflowToolReleaseRef(SchemaBase):
    """Upload-facing reference to a workflow tool code state."""

    name: str = Field(min_length=1)
    version: str | None = None
    git_commit: str | None = Field(default=None, min_length=1, max_length=40)
    release_date: date | None = None
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_required_text(value)

    @model_validator(mode="after")
    def normalize_optional_fields(self) -> Self:
        self.version = normalize_optional_text(self.version)
        self.git_commit = normalize_optional_text(self.git_commit)
        self.notes = normalize_optional_text(self.notes)
        return self


class LevelOfTheoryRef(SchemaBase):
    """Upload-facing reference to a level of theory."""

    method: str = Field(min_length=1)
    basis: str | None = None
    aux_basis: str | None = None
    cabs_basis: str | None = None
    dispersion: str | None = None
    solvent: str | None = None
    solvent_model: str | None = None
    keywords: str | None = None

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        return normalize_required_text(value)

    @model_validator(mode="after")
    def normalize_optional_fields(self) -> Self:
        self.basis = normalize_optional_text(self.basis)
        self.aux_basis = normalize_optional_text(self.aux_basis)
        self.cabs_basis = normalize_optional_text(self.cabs_basis)
        self.dispersion = normalize_optional_text(self.dispersion)
        self.solvent = normalize_optional_text(self.solvent)
        self.solvent_model = normalize_optional_text(self.solvent_model)
        self.keywords = normalize_optional_text(self.keywords)
        return self


class SoftwareRef(SchemaBase):
    """Upload-facing reference to a software package (name only, no version).

    Used when the relevant identifier is the software product rather than
    a specific release — for example, the software context of a frequency
    scale factor entry.
    """

    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_required_text(value)


class FreqScaleFactorRef(SchemaBase):
    """Content-keyed reference to a frequency scale factor.

    The service layer finds or creates the immutable
    ``frequency_scale_factor`` registry row whose identity matches the
    supplied fields. Identity is the full tuple
    ``(level_of_theory, software, scale_kind, value, source_literature,
    workflow_tool_release)`` and matches the DB unique index on
    ``frequency_scale_factor``. ``note`` is descriptive only and never
    participates in identity/dedupe.

    Source handling:

    * If structured literature is available, pass ``source_literature``;
      it is resolved/created via the standard literature pipeline.
    * If only a citation string is available, pass it in ``note`` and
      leave ``source_literature`` null. Do not synthesize placeholder
      literature rows from raw citation strings.
    * If a workflow tool's curated data file is the proximate source,
      pass ``workflow_tool_release`` and put any descriptive file/source
      reference in ``note``.

    Null ``frequency_scale_factor_id`` on a statmech row means
    "unknown/not recorded". Pass ``value=1.0`` with no source to represent
    explicitly unscaled (a real registry row exists, just with value 1.0).

    :param level_of_theory: Level of theory this factor applies to.
    :param scale_kind: Type of scaling (fundamental, ZPE, enthalpy, etc.).
    :param value: The scale factor value.
    :param software: The ESS software the factor applies to (e.g.
        Gaussian). Null means software-agnostic or unknown.
    :param source_literature: Structured literature provenance, when
        available. Mutually informative with ``workflow_tool_release``;
        either, both, or neither may be supplied.
    :param workflow_tool_release: Workflow tool (e.g. ARC) whose data
        file was the proximate source, when the factor was looked up
        from a tool table rather than directly from a paper.
    :param note: Optional descriptive note. Never used for dedupe.
    """

    level_of_theory: LevelOfTheoryRef
    scale_kind: FrequencyScaleKind = FrequencyScaleKind.fundamental
    value: float = Field(gt=0)
    software: SoftwareRef | None = None
    source_literature: "LiteratureUploadRequest | None" = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


# Resolve the forward ref now that the class body is closed.
from tckdb_schemas.literature import LiteratureUploadRequest  # noqa: E402

FreqScaleFactorRef.model_rebuild()
