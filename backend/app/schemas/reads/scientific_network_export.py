"""Read schemas for ``POST /scientific/networks/{network_ref}/kinetics/export-selected``.

Serialises one verified network selection. The request carries the decision manifest the caller saved from
``.../kinetics/select/manifest``, the node it chose and exactly one representation for every member of that node. It
carries no rule, no verdict the server should believe and no database id: the server replays the manifest, re-runs
the selection against its own content and the caller's authorised population, and refuses anything stale, forged or
incomplete. See ``docs/guides/selecting_network_kinetics.md``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.reads.scientific_common import ProfiledRequestEcho


class NetworkSelectedKineticsExportRequest(BaseModel):
    """Request body. ``allow_administrative_choice`` is false unless the caller says otherwise."""

    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any] = Field(
        description="The decision manifest from .../kinetics/select/manifest, exactly as downloaded."
    )
    node_ref: str = Field(
        min_length=1,
        max_length=256,
        description="The chosen node: a determination ref, or '<solve ref>/<product set key>' for a bundle.",
    )
    representation_refs: list[str] = Field(
        min_length=1,
        max_length=10000,
        description="Exactly one eligible fitted representation (a public network-kinetics ref) per member.",
    )
    format: Literal["native", "chemkin"] = "native"
    allow_administrative_choice: bool = Field(
        default=False,
        description="Accept exporting one of several unranked leading alternatives. It never bypasses a conflict, "
        "an incomplete membership or an unsupported serialisation.",
    )
    energy_units: Literal["cal/mol", "kcal/mol", "j/mol", "kj/mol", "k"] = Field(
        default="cal/mol", description="CHEMKIN REACTIONS energy unit. An unknown unit is refused, never replaced."
    )
    include_reported: bool = Field(
        default=False,
        description="CHEMKIN only: a solve of kind 'reported' (rates transcribed from a publication) is written into a "
        "mechanism only when this is true, and then annotated with its literature (ADR 0010).",
    )
    naming_policy: Literal["formula", "public_ref"] = "formula"

    @field_validator("representation_refs")
    @classmethod
    def _refs_only(cls, refs: list[str]) -> list[str]:
        if any(not ref or ref.isdigit() for ref in refs):
            raise ValueError("invalid_handle: representation_refs are public refs, never integer ids")
        return refs


class NetworkExportRequestEcho(ProfiledRequestEcho):
    """What was asked, with the read profile it was answered under."""

    network_ref: str
    node_ref: str
    format: Literal["native", "chemkin"]
    allow_administrative_choice: bool


class NetworkSelectedKineticsExport(BaseModel):
    """The serialised selection, with the provenance and assumptions that bound it."""

    request: NetworkExportRequestEcho
    format: Literal["native", "chemkin"]
    network_ref: str
    node_ref: str
    solve_ref: str
    selection_basis: str = Field(
        description="The selection's outcome, or 'administrative_choice' when the caller accepted one of several "
        "unranked alternatives (not a method claim)."
    )
    administrative: bool
    provenance: dict[str, Any]
    assumptions: list[str]
    members: list[dict[str, Any]] = Field(
        description="Each member of the node: determination, channel with directed endpoints, and the one chosen "
        "representation with its stored numbers."
    )
    files: dict[str, str] | None = Field(default=None, description="CHEMKIN files by name; null for a native export.")
    equation_collisions: list[dict[str, Any]] = Field(
        default_factory=list, description="Chosen channels sharing both endpoints. Reported, never summed."
    )
