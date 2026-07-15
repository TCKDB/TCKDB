"""Upload payloads for group-additivity (Benson) estimation provenance.

Nested fragment of the thermo upload flow (like the energy-correction
payloads). There is no standalone route: a GA breakdown is only meaningful
attached to an estimated ``thermo`` record, so it rides inside
``ThermoUploadRequest.group_additivity`` and is persisted by
``app.services.group_additivity_resolution`` when the parent thermo lands.

No database FK ids are exposed here (schema rule "No FK IDs in upload
schemas"): the scheme is referenced by scientific identity (name + version)
and resolved / deduped server-side, and the ``thermo`` link is supplied by
the workflow from the just-persisted thermo row.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from app.db.models.common import GroupAdditivityComponentKind
from app.schemas.common import SchemaBase
from app.schemas.utils import normalize_optional_text, normalize_required_text
from app.schemas.workflows.literature_upload import LiteratureUploadRequest


class GroupAdditivitySchemeRef(SchemaBase):
    """Upload-facing reference to a group-additivity scheme / library.

    If a matching scheme already exists (by ``name`` + ``version``) it is
    reused; otherwise a new scheme is created. ``code_commit`` records the
    estimator code / group-database revision the values came from and is
    provenance only, never a trust signal.
    """

    name: str = Field(min_length=1)
    version: str | None = None
    description: str | None = None
    source_literature: LiteratureUploadRequest | None = None
    code_commit: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
        self.name = normalize_required_text(self.name)
        self.version = normalize_optional_text(self.version)
        self.description = normalize_optional_text(self.description)
        self.code_commit = normalize_optional_text(self.code_commit)
        self.note = normalize_optional_text(self.note)
        return self


class GroupAdditivityComponentPayload(SchemaBase):
    """One Benson-group (or correction) contribution in a GA breakdown.

    Contributions use fixed-unit columns per the unit policy: kJ/mol for the
    enthalpy contribution, J/(mol*K) for entropy and 298 K heat capacity.
    """

    component_kind: GroupAdditivityComponentKind = GroupAdditivityComponentKind.group
    group_label: str = Field(min_length=1)
    count: int = Field(default=1, ge=1)
    h298_contribution_kj_mol: float | None = None
    s298_contribution_j_mol_k: float | None = None
    cp298_contribution_j_mol_k: float | None = None

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
        self.group_label = normalize_required_text(self.group_label)
        return self


class AppliedGroupAdditivityUploadPayload(SchemaBase):
    """A group-additivity estimation attached to an estimated thermo record."""

    scheme: GroupAdditivitySchemeRef
    note: str | None = None
    components: list[GroupAdditivityComponentPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_has_components(self) -> Self:
        if not self.components:
            raise ValueError(
                "A group-additivity breakdown must include at least one "
                "component contribution."
            )
        return self
