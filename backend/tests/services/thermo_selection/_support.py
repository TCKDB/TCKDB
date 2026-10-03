"""Builders shared by the thermo-selection tests.

Two kinds: plain :class:`NormalizedCandidate` values for the pure rule and engine tests, and
ORM rows (species from the E1 manifest, thermo records with declarations) for the database
tests. ``created_at`` is always set explicitly: every row in one test transaction would
otherwise share the same ``now()``, and recency could not be exercised.
"""

from __future__ import annotations

import zlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.chemistry.thermo_rules import e1_manifest
from app.db.models.common import (
    EnthalpyReferenceKind,
    PhaseKind,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
    ThermoTargetKind,
)
from app.db.models.species import SpeciesEntry
from app.db.models.thermo import Thermo
from app.services.thermo_selection.models import NormalizedCandidate, Subject
from tests.services.scientific_read._factories import make_species, make_species_entry, set_review

T0 = datetime(2026, 6, 1, 12, 0, 0)

_RAW = yaml.safe_load(Path(e1_manifest.MANIFEST_PATH).read_text(encoding="utf-8"))
MEMBERS = {m["member_id"]: m for m in _RAW["members"]}
BY_NAME = {m["common_name"]: m for m in _RAW["members"]}


def protocol(
    recipe: str | None = "g4",
    *,
    departures: list[dict] | None = (),  # type: ignore[assignment]
    derivation: str | None = "atomization",
    internal_motion: str | None = None,
    other_name: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """A stored (version 1) protocol declaration. ``departures=None`` states nothing; ``()`` states none."""
    body: dict[str, Any] = {"version": 1, "departures": None if departures is None else list(departures)}
    if recipe is not None:
        body["recipe"] = {"name": recipe}
        if other_name is not None:
            body["recipe"]["other_name"] = other_name
        if label is not None:
            body["recipe"]["recipe_version"] = label
    if derivation is not None:
        body["formation_reference"] = {"derivation": derivation}
    if internal_motion is not None:
        body["thermal_approximation"] = {"internal_motion": internal_motion}
    return body


def subject_for(name: str, *, multiplicity: int | None = None, **overrides: Any) -> Subject:
    """The :class:`Subject` of a manifest member (by common name), optionally with another multiplicity."""
    m = BY_NAME[name]
    facts: dict[str, Any] = {
        "species_entry_ref": f"sp_{name}",
        "inchi_key": m["inchikey"],
        "molecular_formula": m["molecular_formula"],
        "charge": 0,
        "multiplicity": m["multiplicity"] if multiplicity is None else multiplicity,
        "isotope_key": None,
        "electronic_state_kind": "ground",
        "entry_kind": "minimum",
    }
    facts.update(overrides)
    return Subject(**facts)


def cand(
    ref: str,
    *,
    proto: dict[str, Any] | None | str = "g4",
    status: RecordReviewStatus = RecordReviewStatus.approved,
    age_days: float = 0,
    id_rank: int | None = None,
    origin: str = "computed",
    linked: tuple[str, ...] = (),
) -> NormalizedCandidate:
    """A normalised candidate for the pure tests. ``proto`` is a recipe name, a dict, or ``None`` (undeclared)."""
    if isinstance(proto, str):
        proto = protocol(proto)
    return NormalizedCandidate(
        thermo_ref=ref,
        review_status=status,
        created_at=T0 - timedelta(days=age_days),
        id_rank=id_rank if id_rank is not None else zlib.crc32(ref.encode()),
        scientific_origin=origin,
        phase="gas",
        enthalpy_reference_kind="formation_298k",
        target_kind="equilibrium_ensemble",
        target_group_ref=None,
        protocol_state="absent" if proto is None else "valid",
        protocol=proto,
        linked_recipe_keys=linked,
    )


def species_entry_for(session, name: str, *, multiplicity: int | None = None) -> SpeciesEntry:
    """A species entry for a manifest member, with the member's own InChIKey and SMILES."""
    m = BY_NAME[name]
    species = make_species(
        session,
        smiles=m["smiles"],
        inchi_key=m["inchikey"],
        multiplicity=m["multiplicity"] if multiplicity is None else multiplicity,
    )
    return make_species_entry(session, species)


def make_thermo(
    session,
    entry: SpeciesEntry,
    *,
    h298: float | None = -74.6,
    proto: dict[str, Any] | None = None,
    age_days: float = 0,
    status: RecordReviewStatus | None = None,
    origin: ScientificOriginKind = ScientificOriginKind.computed,
    phase: PhaseKind | None = PhaseKind.gas,
    target: ThermoTargetKind | None = ThermoTargetKind.equilibrium_ensemble,
    group_id: int | None = None,
    reference: EnthalpyReferenceKind | None = EnthalpyReferenceKind.formation_298k,
    tmin_k: float | None = None,
    tmax_k: float | None = None,
    reference_pressure_bar: float | None = None,
    energy_lot_id: int | None = None,
) -> Thermo:
    """A thermo row. ``proto`` is stored as the protocol declaration (``None`` stores none)."""
    thermo = Thermo(
        species_entry_id=entry.id,
        scientific_origin=origin,
        h298_kj_mol=h298,
        enthalpy_reference_kind=reference,
        phase=phase,
        thermodynamic_target_kind=target,
        target_conformer_group_id=group_id,
        protocol_declaration=proto,
        tmin_k=tmin_k,
        tmax_k=tmax_k,
        reference_pressure_bar=reference_pressure_bar,
        energy_level_of_theory_id=energy_lot_id,
        created_at=T0 - timedelta(days=age_days),
    )
    session.add(thermo)
    session.flush()
    if status is not None:
        set_review(session, record_type=SubmissionRecordType.thermo, record_id=thermo.id, status=status)
    return thermo
