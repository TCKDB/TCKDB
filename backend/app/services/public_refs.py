"""Public reference (handle) generation for hosted TCKDB scientific reads.

Implements Phase A of the public identifier policy. See
``docs/specs/public_identifier_policy.md`` for the contract.

Two ref categories per spec:

- **Content-derived refs** (identity-bearing tables: LoT, species,
  chem_reaction, geometry, software, software_release, workflow_tool,
  workflow_tool_release, literature, conformer_assignment_scheme,
  energy_correction_scheme, frequency_scale_factor) — same canonical
  identity → same ref on any TCKDB instance. Computed from existing
  canonical hashes (``lot_hash``, ``geom_hash``, ``stoichiometry_hash``)
  or from canonicalized identity-field tuples.

- **Opaque refs** (event/provenance tables: species_entry,
  reaction_entry, calculation, kinetics, thermo, statmech, transport,
  conformer_group, conformer_observation, transition_state,
  transition_state_entry, submission) — 130 random bits, base32
  lowercase, prefixed by record type.

Both forms produce a 26-character body after the prefix to match
ULID character length. Per the spec, the bodies are URL-safe and
case-insensitive (always lowercase).

Phase A scope: helpers only. No API responses change. No
``include=internal_ids`` token. Public refs are populated at row
INSERT time via a global SQLAlchemy ``before_insert`` listener wired
in at the bottom of this module.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy import event, inspect
from sqlalchemy.orm import Mapper, Session

if TYPE_CHECKING:
    from app.db.base import Base


# ---------------------------------------------------------------------------
# Prefix registry
# ---------------------------------------------------------------------------


# Per-class prefix registry. Keyed by ORM class name (string) so this
# module can be imported without pulling in every model module.
PREFIXES: dict[str, str] = {
    # Identity (content-derived)
    "Species": "spc",
    "ChemReaction": "rxn",
    "Geometry": "geom",
    "LevelOfTheory": "lot",
    "Software": "soft",
    "SoftwareRelease": "srel",
    "WorkflowTool": "wft",
    "WorkflowToolRelease": "wfr",
    "Literature": "lit",
    "ConformerAssignmentScheme": "cas",
    "FrequencyScaleFactor": "fsf",
    "EnergyCorrectionScheme": "ecs",
    # Events / provenance (opaque)
    "SpeciesEntry": "spe",
    "ReactionEntry": "rxe",
    "Calculation": "calc",
    "Thermo": "thm",
    "Kinetics": "kin",
    "Statmech": "sm",
    "Transport": "trn",
    "ConformerGroup": "cg",
    "ConformerObservation": "co",
    "TransitionState": "ts",
    "TransitionStateEntry": "tse",
    "Submission": "sub",
}

# Classes whose ref is content-derived from existing identity columns.
_CONTENT_DERIVED: set[str] = {
    "Species",
    "ChemReaction",
    "Geometry",
    "LevelOfTheory",
    "Software",
    "SoftwareRelease",
    "WorkflowTool",
    "WorkflowToolRelease",
    "Literature",
    "ConformerAssignmentScheme",
    "FrequencyScaleFactor",
    "EnergyCorrectionScheme",
}

# Maximum stored length: longest prefix (4 chars) + underscore + 26 chars body.
PUBLIC_REF_LEN = 32


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def _b32(data: bytes) -> str:
    """Lowercase base32 encoding, no padding. Returns up to 26 chars from
    16 bytes of input.
    """
    raw = base64.b32encode(data).decode("ascii").lower().rstrip("=")
    return raw[:26]


def make_content_ref(prefix: str, canonical_identity: str | bytes) -> str:
    """Deterministic content-derived ref.

    Same ``canonical_identity`` always produces the same ref. Use for
    identity-bearing tables (LoT, species, software, etc.) where the
    same scientific entity should resolve consistently across instances.

    :param prefix: short type prefix (e.g. ``"lot"``, ``"spc"``).
    :param canonical_identity: stable canonical identity string or bytes.
    :returns: ``f"{prefix}_{base32_lowercase_truncated_to_26}"``.
    """
    if isinstance(canonical_identity, str):
        canonical_identity = canonical_identity.encode("utf-8")
    digest = hashlib.sha256(canonical_identity).digest()[:16]
    return f"{prefix}_{_b32(digest)}"


def make_opaque_ref(prefix: str) -> str:
    """Generate an opaque, random ref for event/provenance tables.

    Uses 130 random bits via ``secrets.token_bytes(16)`` → base32
    lowercase, truncated to 26 characters to match the visual format of
    a ULID. Per-instance unique; not stable across instances.

    :param prefix: short type prefix (e.g. ``"calc"``, ``"thm"``).
    """
    return f"{prefix}_{_b32(secrets.token_bytes(16))}"


# ---------------------------------------------------------------------------
# Per-class canonical identity extraction
# ---------------------------------------------------------------------------


def _canonical_lot(obj: Any) -> str:
    """LoT identity: prefer the existing ``lot_hash``, fall back to
    canonical-tuple form so unsaved instances can still be hashed.
    """
    if getattr(obj, "lot_hash", None):
        return f"lot_hash:{obj.lot_hash}"
    parts = [
        ("method", obj.method or ""),
        ("basis", obj.basis or ""),
        ("aux_basis", obj.aux_basis or ""),
        ("cabs_basis", obj.cabs_basis or ""),
        ("dispersion", obj.dispersion or ""),
        ("solvent", obj.solvent or ""),
        ("solvent_model", obj.solvent_model or ""),
        ("keywords", obj.keywords or ""),
    ]
    return "lot:" + "|".join(f"{k}={v}" for k, v in parts)


def _canonical_species(obj: Any) -> str:
    """Species identity: ``(inchi_key, charge, multiplicity, stereo_kind)``."""
    return (
        f"species:inchi_key={obj.inchi_key};"
        f"charge={obj.charge};"
        f"multiplicity={obj.multiplicity};"
        f"stereo_kind={getattr(obj.stereo_kind, 'value', obj.stereo_kind)}"
    )


def _canonical_chem_reaction(obj: Any) -> str:
    """ChemReaction identity: prefer ``stoichiometry_hash`` if populated."""
    sh = getattr(obj, "stoichiometry_hash", None)
    if sh:
        return f"chem_reaction:stoichiometry_hash={sh}"
    # Fallback for transient instances pre-flush — caller is expected
    # to populate stoichiometry_hash before insert.
    rev = getattr(obj, "reversible", None)
    fam = getattr(obj, "reaction_family_id", None)
    return f"chem_reaction:reversible={rev};family_id={fam};id={id(obj)}"


def _canonical_geometry(obj: Any) -> str:
    """Geometry identity: existing ``geom_hash``."""
    return f"geometry:geom_hash={obj.geom_hash}"


def _canonical_software(obj: Any) -> str:
    """Software identity: lowercased trimmed name."""
    name = (obj.name or "").strip().lower()
    return f"software:name={name}"


def _canonical_software_release(obj: Any) -> str:
    """SoftwareRelease identity: software_id + version/revision/build/release_date.

    Uses the FK ``software_id`` rather than re-canonicalizing the
    Software row so two instances with the same software get the same
    ref iff they also resolved to the same software identity.
    """
    return (
        f"software_release:software_id={obj.software_id};"
        f"version={obj.version or ''};"
        f"revision={obj.revision or ''};"
        f"build={obj.build or ''};"
        f"release_date={obj.release_date.isoformat() if obj.release_date else ''}"
    )


def _canonical_workflow_tool(obj: Any) -> str:
    name = (obj.name or "").strip().lower()
    return f"workflow_tool:name={name}"


def _canonical_workflow_tool_release(obj: Any) -> str:
    return (
        f"workflow_tool_release:workflow_tool_id={obj.workflow_tool_id};"
        f"version={obj.version or ''};"
        f"git_commit={obj.git_commit or ''};"
        f"release_date={obj.release_date.isoformat() if obj.release_date else ''}"
    )


def _normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    s = doi.strip().lower()
    for prefix in ("https://dx.doi.org/", "http://dx.doi.org/", "https://doi.org/", "http://doi.org/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s


def _canonical_literature(obj: Any) -> str | None:
    """Literature identity: prefer normalized DOI > normalized ISBN.

    When neither DOI nor ISBN is supplied we cannot safely produce a
    content-derived ref — two physically distinct catalog entries can
    share kind/title/year/journal (different volumes, different
    editions, errata, conference repeats). Returning ``None`` signals
    the dispatcher to fall back to an opaque ref. This matches the spec:
    content-derived where canonical content exists, opaque otherwise.
    """
    doi = _normalize_doi(obj.doi)
    if doi:
        return f"literature:doi={doi}"
    isbn = (obj.isbn or "").replace("-", "").replace(" ", "").lower()
    if isbn:
        return f"literature:isbn={isbn}"
    return None


def _canonical_conformer_assignment_scheme(obj: Any) -> str:
    return (
        f"cas:name={(obj.name or '').strip().lower()};"
        f"version={(obj.version or '').strip().lower()};"
        f"scope={getattr(obj.scope, 'value', obj.scope)}"
    )


def _canonical_frequency_scale_factor(obj: Any) -> str:
    """FrequencyScaleFactor identity matches the table's natural uniqueness.

    Per the model, the natural identity is the full tuple
    ``(level_of_theory_id, software_id, scale_kind, value,
    source_literature_id, workflow_tool_release_id)``. Two rows with the
    same tuple are the same physical scale factor; different ``value``
    or different provenance sources are different rows and must get
    different refs.
    """
    return (
        f"fsf:level_of_theory_id={obj.level_of_theory_id};"
        f"software_id={obj.software_id};"
        f"scale_kind={getattr(obj.scale_kind, 'value', obj.scale_kind)};"
        f"value={obj.value};"
        f"source_literature_id={obj.source_literature_id};"
        f"workflow_tool_release_id={obj.workflow_tool_release_id}"
    )


def _canonical_energy_correction_scheme(obj: Any) -> str:
    """EnergyCorrectionScheme identity must include every field the
    resolver/database treats as part of the row's identity, plus the
    fields the schema considers metadata of a distinct scheme version.

    The database uniqueness constraint and
    ``resolve_or_create_scheme`` both dedup on
    ``(kind, name, level_of_theory_id, version)``; ``source_literature_id``
    and ``units`` are not part of that key but a different value of
    either still means a scientifically distinct scheme (different
    citation, different unit convention). Two rows that the resolver
    treats as distinct must therefore get distinct refs — otherwise
    the ``ix_energy_correction_scheme_public_ref`` unique index trips
    on insert.
    """
    return (
        f"ecs:kind={getattr(obj.kind, 'value', obj.kind)};"
        f"name={(obj.name or '').strip().lower()};"
        f"level_of_theory_id={obj.level_of_theory_id};"
        f"source_literature_id={obj.source_literature_id};"
        f"version={(obj.version or '').strip().lower()};"
        f"units={getattr(obj.units, 'value', obj.units)}"
    )


# Dispatch table from class name → canonical-identity extractor.
_CANONICALIZERS: dict[str, Callable[[Any], str]] = {
    "Species": _canonical_species,
    "ChemReaction": _canonical_chem_reaction,
    "Geometry": _canonical_geometry,
    "LevelOfTheory": _canonical_lot,
    "Software": _canonical_software,
    "SoftwareRelease": _canonical_software_release,
    "WorkflowTool": _canonical_workflow_tool,
    "WorkflowToolRelease": _canonical_workflow_tool_release,
    "Literature": _canonical_literature,
    "ConformerAssignmentScheme": _canonical_conformer_assignment_scheme,
    "FrequencyScaleFactor": _canonical_frequency_scale_factor,
    "EnergyCorrectionScheme": _canonical_energy_correction_scheme,
}


# ---------------------------------------------------------------------------
# Per-row dispatch
# ---------------------------------------------------------------------------


def generate_ref_for(obj: Any) -> str:
    """Return the public ref appropriate for ``obj``.

    Dispatches by the ORM class name:

    - Content-derived classes use ``make_content_ref`` over the per-class
      canonical identity (see ``_CANONICALIZERS``).
    - Event/opaque classes use ``make_opaque_ref``.

    :raises ValueError: if the object's class name is not in PREFIXES,
        i.e. the class isn't on the Phase A ref-bearing list.
    """
    cls_name = type(obj).__name__
    prefix = PREFIXES.get(cls_name)
    if prefix is None:
        raise ValueError(
            f"{cls_name!r} is not a public-ref-bearing class. "
            "Add it to PREFIXES in app.services.public_refs first."
        )
    if cls_name in _CONTENT_DERIVED:
        canonical = _CANONICALIZERS[cls_name](obj)
        # Some canonicalizers (notably literature without DOI/ISBN)
        # legitimately return None when the row's canonical content is
        # incomplete — fall back to an opaque ref in that case.
        if canonical is not None:
            return make_content_ref(prefix, canonical)
    return make_opaque_ref(prefix)


def ensure_public_ref(obj: Any) -> str:
    """If ``obj.public_ref`` is unset, generate and assign one.

    Returns the ref currently on the object. Safe to call repeatedly —
    once a ref is set it is never overwritten (even if the underlying
    canonical identity changes later, which would be a separate
    integrity problem).
    """
    current = getattr(obj, "public_ref", None)
    if current:
        return current
    ref = generate_ref_for(obj)
    obj.public_ref = ref
    return ref


# ---------------------------------------------------------------------------
# SQLAlchemy event listener — wired in app.db.base when Base is imported
# ---------------------------------------------------------------------------


def install_public_ref_listener() -> None:
    """Install a global ``before_insert`` listener that auto-populates
    ``public_ref`` on any ref-bearing ORM row that doesn't already have one.

    Idempotent: safe to call multiple times; the second call is a no-op
    because SQLAlchemy short-circuits duplicate listener registration
    for the same callable.
    """
    @event.listens_for(Mapper, "before_insert")
    def _before_insert(mapper, connection, target):  # noqa: ARG001
        cls_name = type(target).__name__
        if cls_name not in PREFIXES:
            return
        if not getattr(target, "public_ref", None):
            target.public_ref = generate_ref_for(target)


# ---------------------------------------------------------------------------
# Backfill helper
# ---------------------------------------------------------------------------


def backfill_public_refs(session: Session) -> dict[str, int]:
    """Fill missing ``public_ref`` rows for every ref-bearing class.

    Returns a dict of ``{table_name: filled_count}``. Safe to run against
    a database where most rows already have refs; only rows with NULL
    refs are updated. Callers must commit the session.

    Intended for use against existing dev DBs after the initial migration
    has been edited to add public_ref columns; the test fixture rebuilds
    the DB per session so test rows pick up refs at INSERT time via the
    event listener instead.
    """
    # Imports done lazily so this module stays importable from places
    # that don't have the full ORM available (e.g. Alembic env).
    from app.db.models.calculation import Calculation
    from app.db.models.energy_correction import (
        EnergyCorrectionScheme,
        FrequencyScaleFactor,
    )
    from app.db.models.geometry import Geometry
    from app.db.models.kinetics import Kinetics
    from app.db.models.level_of_theory import LevelOfTheory
    from app.db.models.literature import Literature
    from app.db.models.reaction import ChemReaction, ReactionEntry
    from app.db.models.software import Software, SoftwareRelease
    from app.db.models.species import (
        ConformerAssignmentScheme,
        ConformerGroup,
        ConformerObservation,
        Species,
        SpeciesEntry,
    )
    from app.db.models.statmech import Statmech
    from app.db.models.submission import Submission
    from app.db.models.thermo import Thermo
    from app.db.models.transition_state import (
        TransitionState,
        TransitionStateEntry,
    )
    from app.db.models.transport import Transport
    from app.db.models.workflow import WorkflowTool, WorkflowToolRelease

    classes = [
        Species, SpeciesEntry,
        ChemReaction, ReactionEntry,
        Thermo, Kinetics,
        Calculation,
        Geometry,
        ConformerGroup, ConformerObservation, ConformerAssignmentScheme,
        Statmech, Transport,
        TransitionState, TransitionStateEntry,
        LevelOfTheory,
        Software, SoftwareRelease,
        WorkflowTool, WorkflowToolRelease,
        Literature,
        FrequencyScaleFactor, EnergyCorrectionScheme,
        Submission,
    ]

    counts: dict[str, int] = {}
    for cls in classes:
        rows = session.query(cls).filter(cls.public_ref.is_(None)).all()
        n = 0
        for row in rows:
            row.public_ref = generate_ref_for(row)
            n += 1
        counts[cls.__tablename__] = n
        if rows:
            session.flush()
    return counts


__all__ = [
    "PREFIXES",
    "PUBLIC_REF_LEN",
    "make_content_ref",
    "make_opaque_ref",
    "generate_ref_for",
    "ensure_public_ref",
    "install_public_ref_listener",
    "backfill_public_refs",
]
