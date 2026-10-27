from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    ConformerAssignmentScopeKind,
    ConformerSelectionKind,
    MoleculeKind,
    ReactionRole,
    ScientificOriginKind,
    SpeciesEntryReviewRole,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
    StereoResolution,
)
from app.db.types import RDKitMol

if TYPE_CHECKING:
    from app.db.models.app_user import AppUser
    from app.db.models.calculation import Calculation
    from app.db.models.reaction import ReactionParticipant
    from app.db.models.statmech import Statmech
    from app.db.models.thermo import Thermo
    from app.db.models.transport import Transport


class Species(Base, TimestampMixin, PublicRefMixin):
    """Store graph-defined species identities without resolved stereo or 3D conformers."""

    __tablename__ = "species"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[MoleculeKind] = mapped_column(
        SAEnum(MoleculeKind, name="molecule_kind"),
        nullable=False,
    )
    smiles: Mapped[str] = mapped_column(Text, nullable=False)
    inchi_key: Mapped[str] = mapped_column(CHAR(27), nullable=False)
    charge: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    multiplicity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    stereo_kind: Mapped[StereoKind] = mapped_column(
        SAEnum(StereoKind, name="stereo_kind"),
        nullable=False,
    )

    #: The species this row was merged into, or ``NULL`` for a live row (DR-0038).
    #: A species keyed on stereo marks (``C/C=C/C``, ``CC=CC``) and another keyed on
    #: the constitution are one species; the rows that lose are KEPT, with their
    #: public ref and their own curation history, and forward one hop to the survivor.
    #: Results (entries, reaction participants, third-body colliders) were moved to
    #: the survivor. Never set by the application; written by migration
    #: ``c7e2a9f4d138`` and refused to chain by ``trg_species_merge_marker``.
    merged_into_species_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("species.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    entries: Mapped[list["SpeciesEntry"]] = relationship(
        back_populates="species",
        cascade="save-update, merge",
        foreign_keys="SpeciesEntry.species_id",
    )

    reaction_participants: Mapped[list["ReactionParticipant"]] = relationship(
        back_populates="species",
    )

    __table_args__ = (
        # Identity = canonical SMILES + charge + multiplicity (DR-0031).
        # Canonical SMILES distinguishes tautomers that standard InChIKey
        # merges; multiplicity distinguishes spin states that SMILES/InChI
        # cannot encode (singlet vs triplet CH2, O2 states).
        # Unique over LIVE rows only: a merged-away row keeps the SMILES it had and
        # forwards to its survivor (DR-0038).
        Index(
            "uq_species_identity",
            "smiles",
            "charge",
            "multiplicity",
            unique=True,
            postgresql_where=text("merged_into_species_id IS NULL"),
        ),
        # inchi_key stays for cross-notation / external-DB lookup but is
        # non-unique: one InChIKey may map to several species.
        Index("ix_species_inchi_key", "inchi_key"),
        Index("ix_species_merged_into_species_id", "merged_into_species_id"),
        CheckConstraint("multiplicity >= 1", name="multiplicity_ge_1"),
        CheckConstraint(
            "merged_into_species_id IS NULL OR merged_into_species_id <> id",
            name="not_merged_into_itself",
        ),
    )

    @property
    def as_reactant_in(self) -> list["ReactionParticipant"]:
        """Return reaction-participant rows where this species is a reactant."""
        return [
            rp for rp in self.reaction_participants if rp.role == ReactionRole.reactant
        ]

    @property
    def as_product_in(self) -> list["ReactionParticipant"]:
        """Return reaction-participant rows where this species is a product."""
        return [
            rp for rp in self.reaction_participants if rp.role == ReactionRole.product
        ]


class SpeciesEntry(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Store one stereochemically, electronically, or isotopically resolved species form.

    The resolved identity tuple is unique, with nullable identity components
    deduped in PostgreSQL using `NULLS NOT DISTINCT`.
    """

    __tablename__ = "species_entry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    species_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    kind: Mapped[StationaryPointKind] = mapped_column(
        SAEnum(StationaryPointKind, name="stationary_point_kind"),
        nullable=False,
        default=StationaryPointKind.minimum,
        server_default=StationaryPointKind.minimum.value,
    )

    mol: Mapped[Optional[str]] = mapped_column(RDKitMol(), nullable=True)
    unmapped_smiles: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Derived display text for the configuration (DR-0038): the descriptor,
    #: or ``NULL`` when the entry has no stereo element or none is resolved.
    #: Not part of identity and never accepted as a key.
    stereo_label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Canonical stereo descriptor -- the entry's stereo key (DR-0038): one
    #: token per potential stereo element (``R``/``S``/``E``/``Z``/``?``),
    #: ordered by canonical atom rank of the stereo-stripped constitution.
    #: ``NULL`` only when the species is achiral (no stereo element). Partial and
    #: unknown configurations are their own entries (``E,?`` is not ``E,E``).
    stereo_descriptor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Derived: ``resolved`` / ``partial`` / ``unresolved`` from the descriptor's
    #: ``?`` tokens; ``NULL`` iff ``stereo_descriptor`` is.
    stereo_resolution: Mapped[Optional[StereoResolution]] = mapped_column(
        SAEnum(StereoResolution, name="stereo_resolution"),
        nullable=True,
    )

    #: Derived search/display text: canonical isomeric SMILES (isotopes
    #: stripped) of a fully resolved configuration, where one can be written. A
    #: sigma-vinyl radical's E/Z cannot be, so this is NULL there. Not the key.
    stereo_smiles: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: The entry this row was merged into, or ``NULL`` for a live row (DR-0038).
    #: Two entries that name one configuration of one species are one entry. The
    #: row that loses is KEPT -- public ref, reviews, machine reviews, release
    #: selections, supersession edges stay on it -- and forwards one hop to the
    #: survivor, which holds the results (thermo, statmech, calculations, ...).
    merged_into_species_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    electronic_state_kind: Mapped[SpeciesEntryStateKind] = mapped_column(
        SAEnum(SpeciesEntryStateKind, name="species_entry_state_kind"),
        nullable=False,
        default=SpeciesEntryStateKind.ground,
        server_default=SpeciesEntryStateKind.ground.value,
    )
    electronic_state_label: Mapped[Optional[str]] = mapped_column(
        String(8),
        nullable=True,
    )
    term_symbol_raw: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    term_symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    #: Canonical, atom-resolved isotope key — the canonical SMILES of the
    #: isotope-labelled identity molecule (e.g. ``[2H]C([2H])([2H])O``).
    #: ``NULL`` means every atom is at its most abundant natural isotope,
    #: which is the stable all-standard key: it does not depend on RDKit
    #: canonicalization, so pre-existing entries keep their identity exactly.
    #: Derived server-side by
    #: :func:`app.chemistry.species.canonical_isotope_key`; never accepted
    #: from an uploader.
    isotope_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: DEPRECATED free-text isotopologue annotation. It used to participate
    #: in ``uq_species_entry_species_id``, which let a producer mint two
    #: species identities that differed only by an arbitrary string with no
    #: atom-resolved isotope content anywhere in the schema. It is no longer
    #: part of identity, is no longer accepted on any upload/Create/Update
    #: schema, and is never written by the application. The column is
    #: retained (rather than dropped) purely so that a self-hosted or lab
    #: deployment that already stored annotations here does not lose them to
    #: a migration; ``isotope_key`` supersedes it.
    isotopologue_label: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )

    species: Mapped["Species"] = relationship(back_populates="entries")

    conformer_groups: Mapped[list["ConformerGroup"]] = relationship(
        back_populates="species_entry",
        cascade="save-update, merge",
    )

    calculations: Mapped[list["Calculation"]] = relationship(
        back_populates="species_entry",
        foreign_keys="Calculation.species_entry_id",
    )

    thermo_records: Mapped[list["Thermo"]] = relationship(
        back_populates="species_entry",
        cascade="save-update, merge",
        foreign_keys="Thermo.species_entry_id",
    )

    statmech_records: Mapped[list["Statmech"]] = relationship(
        back_populates="species_entry",
        cascade="save-update, merge",
        foreign_keys="Statmech.species_entry_id",
    )
    transport_records: Mapped[list["Transport"]] = relationship(
        back_populates="species_entry",
        cascade="save-update, merge",
        foreign_keys="Transport.species_entry_id",
    )

    reviews: Mapped[list["SpeciesEntryReview"]] = relationship(
        back_populates="species_entry",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_species_entry_species_id", "species_id"),
        Index("ix_species_entry_stereo_smiles", "stereo_smiles"),
        CheckConstraint(
            "stereo_descriptor IS NULL OR stereo_descriptor ~ '^[RSrsEZez?-](,[RSrsEZez?-])*$'",
            name="stereo_descriptor_vocabulary",
        ),
        CheckConstraint(
            "(stereo_descriptor IS NULL) = (stereo_resolution IS NULL)",
            name="stereo_resolution_iff_descriptor",
        ),
        # Unique over LIVE entries; a merged-away entry keeps its key and forwards.
        Index(
            "uq_species_entry_species_id",
            "species_id",
            "stereo_descriptor",
            "electronic_state_kind",
            "electronic_state_label",
            "term_symbol",
            "isotope_key",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("merged_into_species_entry_id IS NULL"),
        ),
        Index("ix_species_entry_merged_into_species_entry_id", "merged_into_species_entry_id"),
        CheckConstraint(
            "merged_into_species_entry_id IS NULL OR merged_into_species_entry_id <> id",
            name="not_merged_into_itself",
        ),
    )


class ConformerGroup(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Store one deduplicated conformational-basin identity for a species entry."""

    __tablename__ = "conformer_group"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    species_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    representative_fingerprint_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    representative_coords_json: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True
    )

    species_entry: Mapped["SpeciesEntry"] = relationship(
        back_populates="conformer_groups"
    )

    observations: Mapped[list["ConformerObservation"]] = relationship(
        back_populates="conformer_group",
        cascade="save-update, merge",
    )

    selections: Mapped[list["ConformerSelection"]] = relationship(
        back_populates="conformer_group",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_conformer_group_species_entry_id", "species_entry_id"),
        UniqueConstraint(
            "species_entry_id",
            "label",
            name="uq_conformer_group_species_entry_id",
        ),
    )


class ConformerObservation(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Store one provenance-bearing conformer observation assigned to a basin."""

    __tablename__ = "conformer_observation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    conformer_group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conformer_group.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    assignment_scheme_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "conformer_assignment_scheme.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_conformer_observation_assignment_scheme_id",
        ),
        nullable=True,
    )

    scientific_origin: Mapped[ScientificOriginKind] = mapped_column(
        SAEnum(ScientificOriginKind, name="scientific_origin_kind"),
        nullable=False,
        default=ScientificOriginKind.computed,
        server_default=ScientificOriginKind.computed.value,
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    torsion_fingerprint_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )

    conformer_group: Mapped["ConformerGroup"] = relationship(
        back_populates="observations"
    )
    calculations: Mapped[list["Calculation"]] = relationship(
        back_populates="conformer_observation",
        foreign_keys="Calculation.conformer_observation_id",
    )
    assignment_scheme: Mapped[Optional["ConformerAssignmentScheme"]] = relationship(
        back_populates="observations"
    )

    __table_args__ = (
        Index("ix_conformer_observation_conformer_group_id", "conformer_group_id"),
    )


class ConformerSelection(Base, TimestampMixin, CreatedByMixin):
    """Store explicit workflow, curation, or UI selections for conformer groups.

    `NULL` assignment-scheme references are treated as identical for dedupe.
    """

    __tablename__ = "conformer_selection"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    conformer_group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conformer_group.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    assignment_scheme_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "conformer_assignment_scheme.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_conformer_selection_assignment_scheme_id",
        ),
        nullable=True,
    )

    selection_kind: Mapped[ConformerSelectionKind] = mapped_column(
        SAEnum(ConformerSelectionKind, name="conformer_selection_kind"),
        nullable=False,
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    conformer_group: Mapped["ConformerGroup"] = relationship(
        back_populates="selections"
    )
    assignment_scheme: Mapped[Optional["ConformerAssignmentScheme"]] = relationship(
        back_populates="selections"
    )

    __table_args__ = (
        UniqueConstraint(
            "conformer_group_id",
            "assignment_scheme_id",
            "selection_kind",
            name="uq_conformer_selection_conformer_group_id",
            postgresql_nulls_not_distinct=True,
        ),
    )


class ConformerAssignmentScheme(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Store versioned metadata about conformer-assignment or selection logic.

    This table is optional in the simplified design. It is useful when the
    application wants to preserve provenance for how conformers were assigned or
    how selections such as lowest-energy/display-default were determined.
    """

    __tablename__ = "conformer_assignment_scheme"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # TCKDB owns the scheme: its version is an integer, never text.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[ConformerAssignmentScopeKind] = mapped_column(
        SAEnum(ConformerAssignmentScopeKind, name="conformer_assignment_scope_kind"),
        nullable=False,
        default=ConformerAssignmentScopeKind.canonical,
        server_default=ConformerAssignmentScopeKind.canonical.value,
    )

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parameters_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )
    code_commit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )

    selections: Mapped[list["ConformerSelection"]] = relationship(
        back_populates="assignment_scheme",
        cascade="all, delete-orphan",
    )
    observations: Mapped[list["ConformerObservation"]] = relationship(
        back_populates="assignment_scheme"
    )

    __table_args__ = (
        UniqueConstraint(
            "name",
            "version",
            name="uq_conformer_assignment_scheme_name",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
    )


class SpeciesEntryReview(Base, TimestampMixin):
    """Store explicit human review or curation events for species entries."""

    __tablename__ = "species_entry_review"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    species_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    role: Mapped[SpeciesEntryReviewRole] = mapped_column(
        SAEnum(SpeciesEntryReviewRole, name="species_entry_review_role"),
        nullable=False,
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    species_entry: Mapped["SpeciesEntry"] = relationship(back_populates="reviews")
    user: Mapped["AppUser"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "species_entry_id",
            "user_id",
            "role",
            name="uq_species_entry_review_species_entry_id",
        ),
    )
