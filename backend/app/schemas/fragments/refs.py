from datetime import date
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.db.models.common import FrequencyScaleKind
from app.schemas.common import SchemaBase
from app.schemas.utils import normalize_optional_text, normalize_required_text


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
    """Upload-facing description of a frequency scale factor definition.

    The service layer will find or create the immutable
    ``frequency_scale_factor`` registry row whose identity matches all
    supplied fields.

    Null ``frequency_scale_factor_id`` on a statmech row means
    "unknown/not recorded".  Pass ``value=1.0`` with no source to represent
    explicitly unscaled (i.e. a real registry row exists, just with value 1.0).

    :param level_of_theory: Level of theory this factor applies to.
    :param scale_kind: Type of scaling (fundamental, ZPE, enthalpy, etc.).
    :param value: The scale factor value.
    :param software: The ESS software the factor applies to (e.g. Gaussian).
        Null means software-agnostic or unknown.
    :param workflow_tool_release: Workflow tool (e.g. ARC) whose data file
        was the proximate source, when the factor was looked up from a tool
        table rather than directly from a paper.
    :param note: Optional note.
    """

    level_of_theory: LevelOfTheoryRef
    scale_kind: FrequencyScaleKind = FrequencyScaleKind.fundamental
    value: float = Field(gt=0)
    software: SoftwareRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self
