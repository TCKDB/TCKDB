"""Chemistry-first lookup endpoints.

These endpoints resolve chemical identities and find existing data.  They
are client-agnostic: any tool (ARC, RMG, notebooks, web UIs, ML pipelines)
can use them to ask "what do you already have?"

Design principles:
- Chemistry is the query language, not database IDs.
- Every response uses the same envelope: ``query``, ``match``, ``results``.
- Match quality is explicit and uses a controlled vocabulary.
- Results are summary-oriented with ``links`` to canonical resources.
- Identity lookup and result lookup are separate concerns.

Match semantics:
- ``match.status``: ``exact`` / ``partial`` / ``none`` (stable public contract)
- ``match.detail_codes``: machine-readable codes for client logic
- ``match.details``: human-readable explanations

Internal match taxonomy (grounded in three axes):
- identity: exact / partial / none
- result_existence: yes / no
- lot: exact / partial / none (per-field: method, basis, dispersion, solvent, ...)

Endpoint families:
- **Identity**: ``/lookup/species``, ``/lookup/reaction``, ``/lookup/geometry``
- **Result**: ``/lookup/calculations``, ``/lookup/thermo``, ``/lookup/kinetics``,
  ``/lookup/statmech``, ``/lookup/transport``
- **Membership**: ``/lookup/network``
- **Composed**: ``/lookup/species-calculation``, ``/lookup/reaction-kinetics``
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.errors import NotFoundError
from app.chemistry.species import canonical_species_identity
from app.db.models.calculation import (
    Calculation,
    CalculationFreqResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import CalculationGeometryRole
from app.db.models.geometry import Geometry
from app.db.models.kinetics import Kinetics
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.network import Network, NetworkSpecies
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionParticipant,
)
from app.db.models.species import Species, SpeciesEntry
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.reaction_resolution import reaction_stoichiometry_hash

router = APIRouter()


# ---------------------------------------------------------------------------
# Detail code vocabulary
# ---------------------------------------------------------------------------

# Identity
SPECIES_IDENTITY_EXACT = "species_identity_exact"
SPECIES_IDENTITY_NONE = "species_identity_none"
SPECIES_ENTRY_EXISTS = "species_entry_exists"
SPECIES_ENTRY_NONE = "species_entry_none"

# Reaction identity
REACTION_IDENTITY_EXACT = "reaction_identity_exact"
REACTION_IDENTITY_NONE = "reaction_identity_none"
REACTION_ENTRY_EXISTS = "reaction_entry_exists"
REACTION_ENTRY_NONE = "reaction_entry_none"
REACTANT_RESOLVED = "reactant_resolved"
REACTANT_NOT_FOUND = "reactant_not_found"
PRODUCT_RESOLVED = "product_resolved"
PRODUCT_NOT_FOUND = "product_not_found"

# Result existence
CALCULATION_EXISTS = "calculation_exists"
CALCULATION_NONE = "calculation_none"
THERMO_EXISTS = "thermo_exists"
THERMO_NONE = "thermo_none"
KINETICS_EXISTS = "kinetics_exists"
KINETICS_NONE = "kinetics_none"
STATMECH_EXISTS = "statmech_exists"
STATMECH_NONE = "statmech_none"
TRANSPORT_EXISTS = "transport_exists"
TRANSPORT_NONE = "transport_none"

# Geometry identity
GEOMETRY_IDENTITY_EXACT = "geometry_identity_exact"
GEOMETRY_IDENTITY_NONE = "geometry_identity_none"

# Network membership
NETWORK_EXISTS = "network_exists"
NETWORK_NONE = "network_none"
NETWORK_MEMBERSHIP_CONTAINS_ALL = "network_membership_contains_all"
SPECIES_ENTRY_NOT_FOUND = "species_entry_not_found"

# LOT field-level codes
LOT_NONE = "lot_none"
LOT_METHOD_EXACT = "lot_method_exact"
LOT_METHOD_MISMATCH = "lot_method_mismatch"
LOT_BASIS_EXACT = "lot_basis_exact"
LOT_BASIS_MISMATCH = "lot_basis_mismatch"
LOT_DISPERSION_PRESENT = "lot_dispersion_present_not_queried"
LOT_SOLVENT_PRESENT = "lot_solvent_present_not_queried"
LOT_SOLVENT_MODEL_PRESENT = "lot_solvent_model_present_not_queried"
LOT_AUX_BASIS_PRESENT = "lot_aux_basis_present_not_queried"


# ---------------------------------------------------------------------------
# Consistent response envelope
# ---------------------------------------------------------------------------


MatchStatus = Literal["exact", "partial", "none"]


class LookupQuery(BaseModel):
    """Echo of what was asked."""

    kind: str
    inputs: dict[str, Any]


class MatchDetail(BaseModel):
    """How the query was matched."""

    status: MatchStatus
    detail_codes: list[str] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)


class ResourceLink(BaseModel):
    """Links to canonical REST resources."""

    self: str
    owner: str | None = None


class LookupResultItem(BaseModel):
    """One matched resource in the results list."""

    resource_type: str
    id: int
    links: ResourceLink
    summary: dict[str, Any]


class LookupResponse(BaseModel):
    """Consistent envelope for all lookup endpoints."""

    query: LookupQuery
    match: MatchDetail
    results: list[LookupResultItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal match builder
# ---------------------------------------------------------------------------


class _MatchBuilder:
    """Accumulates codes + details and derives the public status."""

    def __init__(self):
        self.codes: list[str] = []
        self.details: list[str] = []

    def add(self, code: str, detail: str) -> None:
        self.codes.append(code)
        self.details.append(detail)

    def status(self) -> MatchStatus:
        """Derive public status from accumulated codes.

        Three-axis model:
        - identity exact + result exists + lot exact → exact
        - identity exact + (result none | lot mismatch) → partial
        - identity none → none
        - result-only query with no results → none
        """
        has_identity_exact = (
            SPECIES_IDENTITY_EXACT in self.codes
            or REACTION_IDENTITY_EXACT in self.codes
            or GEOMETRY_IDENTITY_EXACT in self.codes
        )
        has_identity_none = (
            SPECIES_IDENTITY_NONE in self.codes
            or REACTION_IDENTITY_NONE in self.codes
            or GEOMETRY_IDENTITY_NONE in self.codes
            or REACTANT_NOT_FOUND in self.codes
            or PRODUCT_NOT_FOUND in self.codes
            or SPECIES_ENTRY_NOT_FOUND in self.codes
        )
        has_result_none = (
            CALCULATION_NONE in self.codes
            or THERMO_NONE in self.codes
            or KINETICS_NONE in self.codes
            or STATMECH_NONE in self.codes
            or TRANSPORT_NONE in self.codes
            or NETWORK_NONE in self.codes
        )
        has_entry_none = (
            SPECIES_ENTRY_NONE in self.codes
            or REACTION_ENTRY_NONE in self.codes
        )
        has_lot_mismatch = any(c.endswith("_mismatch") for c in self.codes)

        if has_identity_none:
            return "none"

        # Composed queries: identity found but result missing → partial
        if has_identity_exact and (has_result_none or has_entry_none or has_lot_mismatch):
            return "partial"

        # Result-only queries (no identity axis): no results → none
        if has_result_none:
            return "none"

        if has_lot_mismatch:
            return "partial"

        return "exact"

    def build(self) -> MatchDetail:
        return MatchDetail(
            status=self.status(),
            detail_codes=self.codes,
            details=self.details,
        )


# ---------------------------------------------------------------------------
# LOT match logic
# ---------------------------------------------------------------------------


def _lot_match(
    lot: LevelOfTheory | None,
    method: str | None,
    basis: str | None,
    mb: _MatchBuilder,
) -> MatchStatus:
    """Evaluate LOT match quality and append codes/details to the builder."""
    if lot is None:
        mb.add(LOT_NONE, "no level of theory recorded")
        return "none"

    lot_status: MatchStatus = "exact"

    if method is not None:
        if lot.method == method.lower():
            mb.add(LOT_METHOD_EXACT, "method matched exactly")
        else:
            mb.add(LOT_METHOD_MISMATCH, f"method mismatch: have {lot.method}, want {method}")
            lot_status = "partial"

    if basis is not None:
        if lot.basis == basis.lower():
            mb.add(LOT_BASIS_EXACT, "basis matched exactly")
        else:
            mb.add(LOT_BASIS_MISMATCH, f"basis mismatch: have {lot.basis}, want {basis}")
            lot_status = "partial"

    # Report LOT fields that were present but not queried
    if lot.dispersion:
        mb.add(LOT_DISPERSION_PRESENT, f"dispersion={lot.dispersion} (not queried)")
    if lot.solvent:
        mb.add(LOT_SOLVENT_PRESENT, f"solvent={lot.solvent} (not queried)")
    if lot.solvent_model:
        mb.add(LOT_SOLVENT_MODEL_PRESENT, f"solvent_model={lot.solvent_model} (not queried)")
    if lot.aux_basis:
        mb.add(LOT_AUX_BASIS_PRESENT, f"aux_basis={lot.aux_basis} (not queried)")

    return lot_status


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------

#: Valid selection modes.
SELECTION_MODES = {"all", "lowest_energy", "latest", "earliest"}

# Detail codes for selection
SELECTION_APPLIED = "selection_applied"
SELECTION_MIXED_LOT_WARNING = "selection_mixed_lot_warning"


def _apply_selection(
    calcs: list[Calculation],
    selection: str,
    session: Session,
    mb: _MatchBuilder,
) -> list[Calculation]:
    """Apply a selection mode to reduce a list of matched calculations.

    Selection modes:
    - ``all``: no-op, return everything.
    - ``lowest_energy``: lowest final electronic energy (opt result),
      tie-break: lot_match exact first → converged first → newest → lowest ID.
    - ``latest``: most recently created (created_at DESC, then lowest ID).
    - ``earliest``: oldest (created_at ASC, then lowest ID).

    Warns if lowest_energy is applied across mixed levels of theory.
    """
    if selection == "all" or len(calcs) <= 1:
        return calcs

    if selection == "lowest_energy":
        # Check for mixed LOT
        lot_ids = {c.lot_id for c in calcs if c.lot_id is not None}
        if len(lot_ids) > 1:
            mb.add(
                SELECTION_MIXED_LOT_WARNING,
                "lowest_energy applied across mixed levels of theory",
            )

        def _energy_sort_key(c: Calculation):
            # Fetch opt result for energy
            opt = session.scalar(
                select(CalculationOptResult)
                .where(CalculationOptResult.calculation_id == c.id)
            )
            energy = opt.final_energy_hartree if opt and opt.final_energy_hartree is not None else float("inf")
            converged = opt.converged if opt else False
            return (
                energy,                          # lowest energy first
                0 if converged else 1,            # converged before not
                -(c.created_at.timestamp() if c.created_at else 0),  # newest first (negate for ASC sort)
                c.id,                             # lowest ID last resort
            )

        calcs = sorted(calcs, key=_energy_sort_key)
        winner = calcs[0]
        mb.add(SELECTION_APPLIED, f"selection=lowest_energy chose calculation {winner.id}")
        return [winner]

    if selection == "latest":
        calcs = sorted(
            calcs,
            key=lambda c: (-(c.created_at.timestamp() if c.created_at else 0), c.id),
        )
        winner = calcs[0]
        mb.add(SELECTION_APPLIED, f"selection=latest chose calculation {winner.id}")
        return [winner]

    if selection == "earliest":
        calcs = sorted(
            calcs,
            key=lambda c: (c.created_at.timestamp() if c.created_at else float("inf"), c.id),
        )
        winner = calcs[0]
        mb.add(SELECTION_APPLIED, f"selection=earliest chose calculation {winner.id}")
        return [winner]

    return calcs


# ---------------------------------------------------------------------------
# Calculation summary helper
# ---------------------------------------------------------------------------


def _calc_summary(
    calc: Calculation,
    lot: LevelOfTheory | None,
    session: Session,
    lot_match_status: MatchStatus,
    *,
    include_geometry: bool = False,
) -> dict[str, Any]:
    """Build a summary dict for a calculation."""
    summary: dict[str, Any] = {
        "type": calc.type.value if hasattr(calc.type, "value") else str(calc.type),
        "lot_match": lot_match_status,
    }

    if lot:
        summary["method"] = lot.method
        summary["basis"] = lot.basis

    opt_row = session.scalar(
        select(CalculationOptResult)
        .where(CalculationOptResult.calculation_id == calc.id)
    )
    if opt_row:
        summary["converged"] = opt_row.converged
        summary["energy_hartree"] = opt_row.final_energy_hartree
        summary["n_steps"] = opt_row.n_steps

    freq_row = session.scalar(
        select(CalculationFreqResult)
        .where(CalculationFreqResult.calculation_id == calc.id)
    )
    if freq_row:
        summary["n_imag"] = freq_row.n_imag
        summary["imag_freq_cm1"] = freq_row.imag_freq_cm1
        summary["zpe_hartree"] = freq_row.zpe_hartree

    sp_row = session.scalar(
        select(CalculationSPResult)
        .where(CalculationSPResult.calculation_id == calc.id)
    )
    if sp_row:
        summary["electronic_energy_hartree"] = sp_row.electronic_energy_hartree

    if include_geometry:
        calc_type = calc.type.value if hasattr(calc.type, "value") else str(calc.type)
        geom_link = session.scalar(
            select(CalculationOutputGeometry)
            .where(CalculationOutputGeometry.calculation_id == calc.id)
            .where(CalculationOutputGeometry.role == CalculationGeometryRole.final)
        )
        if geom_link:
            geom = session.get(Geometry, geom_link.geometry_id)
            if geom and geom.xyz_text:
                summary["geometry"] = {
                    "geometry_id": geom.id,
                    "source_calculation_id": calc.id,
                    "role": "final",
                    "natoms": geom.natoms,
                    "xyz_text": geom.xyz_text,
                }
            else:
                summary["geometry"] = None
                summary["geometry_status"] = "incomplete"
        elif calc_type in ("freq", "sp"):
            # These calc types don't normally produce geometries
            summary["geometry"] = None
            summary["geometry_status"] = "not_applicable"
        else:
            summary["geometry"] = None
            summary["geometry_status"] = "missing"

    return summary


def _kinetics_summary(kin: Kinetics) -> dict[str, Any]:
    """Build a summary dict for a kinetics record."""
    summary: dict[str, Any] = {
        "model_kind": (
            kin.model_kind.value if hasattr(kin.model_kind, "value") else str(kin.model_kind)
        ),
        "scientific_origin": (
            kin.scientific_origin.value
            if hasattr(kin.scientific_origin, "value")
            else str(kin.scientific_origin)
        ),
    }
    if kin.a is not None:
        summary["a"] = kin.a
    if kin.a_units is not None:
        summary["a_units"] = kin.a_units.value if hasattr(kin.a_units, "value") else str(kin.a_units)
    if kin.n is not None:
        summary["n"] = kin.n
    if kin.ea_kj_mol is not None:
        summary["ea_kj_mol"] = kin.ea_kj_mol
    if kin.tmin_k is not None:
        summary["tmin_k"] = kin.tmin_k
    if kin.tmax_k is not None:
        summary["tmax_k"] = kin.tmax_k
    return summary


def _resolve_species_list(
    smiles_list: list[str],
    charges: list[int],
    multiplicities: list[int],
    role: str,
    session: Session,
    mb: _MatchBuilder,
) -> list[Species] | None:
    """Resolve a list of SMILES into Species rows.

    Returns None if any species cannot be resolved.
    """
    species_rows: list[Species] = []
    for i, (smi, chg, mult) in enumerate(zip(smiles_list, charges, multiplicities)):
        payload = SpeciesEntryIdentityPayload(smiles=smi, charge=chg, multiplicity=mult)
        try:
            _csmi, inchi_key = canonical_species_identity(payload)
        except ValueError as e:
            code = REACTANT_NOT_FOUND if role == "reactant" else PRODUCT_NOT_FOUND
            mb.add(code, f"{role}[{i}] '{smi}': {e}")
            return None
        sp = session.scalar(select(Species).where(Species.inchi_key == inchi_key))
        if sp is None:
            code = REACTANT_NOT_FOUND if role == "reactant" else PRODUCT_NOT_FOUND
            mb.add(code, f"{role}[{i}] '{smi}' not found in database")
            return None
        code = REACTANT_RESOLVED if role == "reactant" else PRODUCT_RESOLVED
        mb.add(code, f"{role}[{i}] '{smi}' resolved to species {sp.id}")
        species_rows.append(sp)
    return species_rows


def _resolve_reaction(
    reactants: list[str],
    products: list[str],
    r_charges: list[int],
    p_charges: list[int],
    r_mults: list[int],
    p_mults: list[int],
    reversible: bool,
    session: Session,
    mb: _MatchBuilder,
) -> tuple[ChemReaction | None, list[LookupResultItem]]:
    """Shared reaction resolution for /lookup/reaction and /lookup/reaction-kinetics."""
    from collections import Counter

    r_species = _resolve_species_list(reactants, r_charges, r_mults, "reactant", session, mb)
    p_species = _resolve_species_list(products, p_charges, p_mults, "product", session, mb)

    if r_species is None or p_species is None:
        mb.add(REACTION_IDENTITY_NONE, "could not resolve all participants")
        return None, []

    r_stoich = dict(Counter(sp.id for sp in r_species))
    p_stoich = dict(Counter(sp.id for sp in p_species))

    stoi_hash = reaction_stoichiometry_hash(
        reversible=reversible, reactants=r_stoich, products=p_stoich,
    )
    chem_rxn = session.scalar(
        select(ChemReaction).where(ChemReaction.stoichiometry_hash == stoi_hash)
    )
    if chem_rxn is None:
        mb.add(REACTION_IDENTITY_NONE, "no reaction with this stoichiometry exists")
        return None, []

    mb.add(REACTION_IDENTITY_EXACT, "reaction identity matched exactly by stoichiometry hash")

    family_name = None
    if chem_rxn.reaction_family:
        family_name = chem_rxn.reaction_family.name
    elif chem_rxn.reaction_family_raw:
        family_name = chem_rxn.reaction_family_raw

    results: list[LookupResultItem] = [LookupResultItem(
        resource_type="reaction",
        id=chem_rxn.id,
        links=ResourceLink(self=f"/api/v1/reactions/{chem_rxn.id}"),
        summary={"reversible": chem_rxn.reversible, "reaction_family": family_name},
    )]
    return chem_rxn, results


def _reaction_entry_result(
    entry: ReactionEntry,
    chem_rxn_id: int,
    session: Session,
) -> LookupResultItem:
    """Build a reaction_entry result item with resolved participant structure."""
    kinetics_count = session.scalar(
        select(func.count(Kinetics.id)).where(Kinetics.reaction_entry_id == entry.id)
    ) or 0

    # Resolve participants from reaction_entry_structure_participant
    participants = session.scalars(
        select(ReactionEntryStructureParticipant)
        .where(ReactionEntryStructureParticipant.reaction_entry_id == entry.id)
        .order_by(
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
        )
    ).all()

    participant_list = []
    for p in participants:
        se = session.get(SpeciesEntry, p.species_entry_id)
        sp = session.get(Species, se.species_id) if se else None

        participant_list.append({
            "side": p.role.value if hasattr(p.role, "value") else str(p.role),
            "index": p.participant_index,
            "species_entry_id": p.species_entry_id,
            "species_id": sp.id if sp else None,
            "smiles": sp.smiles if sp else None,
            "charge": sp.charge if sp else None,
            "multiplicity": sp.multiplicity if sp else None,
            "links": {
                "species_entry": f"/api/v1/species-entries/{p.species_entry_id}",
                "species": f"/api/v1/species/{sp.id}" if sp else None,
            },
        })

    return LookupResultItem(
        resource_type="reaction_entry",
        id=entry.id,
        links=ResourceLink(
            self=f"/api/v1/reaction-entries/{entry.id}",
            owner=f"/api/v1/reactions/{chem_rxn_id}",
        ),
        summary={
            "reaction_id": chem_rxn_id,
            "reaction_entry_id": entry.id,
            "kinetics_count": kinetics_count,
            "participants": participant_list,
        },
    )


# ---------------------------------------------------------------------------
# 1. Identity lookup: /lookup/species
# ---------------------------------------------------------------------------


@router.get("/species", response_model=LookupResponse)
def lookup_species(
    smiles: str = Query(..., description="SMILES string for the species"),
    charge: int = Query(..., description="Formal charge"),
    multiplicity: int = Query(..., ge=1, description="Spin multiplicity"),
    session: Session = Depends(get_db),
):
    """Resolve a species by its chemical identity.

    Uses the same canonicalization as uploads: SMILES → canonical SMILES
    → InChI key.  Returns the species and all matching entries with
    availability summaries.
    """
    inputs = {"smiles": smiles, "charge": charge, "multiplicity": multiplicity}
    query = LookupQuery(kind="species", inputs=inputs)
    mb = _MatchBuilder()

    payload = SpeciesEntryIdentityPayload(
        smiles=smiles, charge=charge, multiplicity=multiplicity,
    )
    try:
        _canonical_smiles, inchi_key = canonical_species_identity(payload)
    except ValueError as e:
        mb.add(SPECIES_IDENTITY_NONE, str(e))
        return LookupResponse(query=query, match=mb.build())

    species = session.scalar(
        select(Species).where(Species.inchi_key == inchi_key)
    )
    if species is None:
        mb.add(SPECIES_IDENTITY_NONE, "no species with this InChI key exists")
        return LookupResponse(query=query, match=mb.build())

    mb.add(SPECIES_IDENTITY_EXACT, "species identity matched exactly by InChI key")

    entries = session.scalars(
        select(SpeciesEntry).where(SpeciesEntry.species_id == species.id)
    ).all()

    results: list[LookupResultItem] = []

    results.append(LookupResultItem(
        resource_type="species",
        id=species.id,
        links=ResourceLink(self=f"/api/v1/species/{species.id}"),
        summary={
            "smiles": species.smiles,
            "inchi_key": species.inchi_key,
            "charge": species.charge,
            "multiplicity": species.multiplicity,
            "entry_count": len(entries),
        },
    ))

    for entry in entries:
        calc_count = session.scalar(
            select(func.count(Calculation.id))
            .where(Calculation.species_entry_id == entry.id)
        ) or 0
        thermo_count = session.scalar(
            select(func.count(Thermo.id))
            .where(Thermo.species_entry_id == entry.id)
        ) or 0

        kind_val = entry.kind.value if hasattr(entry.kind, "value") else str(entry.kind)
        results.append(LookupResultItem(
            resource_type="species_entry",
            id=entry.id,
            links=ResourceLink(
                self=f"/api/v1/species-entries/{entry.id}",
                owner=f"/api/v1/species/{species.id}",
            ),
            summary={
                "kind": kind_val,
                "calculation_count": calc_count,
                "thermo_count": thermo_count,
            },
        ))

    return LookupResponse(query=query, match=mb.build(), results=results)


# ---------------------------------------------------------------------------
# 2. Result lookup: /lookup/calculations
# ---------------------------------------------------------------------------


@router.get("/calculations", response_model=LookupResponse)
def lookup_calculations(
    species_entry_id: int = Query(..., description="Species entry to search"),
    type: str | None = Query(None, description="Calculation type: opt, freq, sp, irc, scan"),
    method: str | None = Query(None, description="Level of theory method"),
    basis: str | None = Query(None, description="Basis set"),
    include: list[str] = Query(default=[], description="Optional expansions: geometry"),
    selection: str = Query("all", description="Selection mode: all, lowest_energy, latest, earliest"),
    session: Session = Depends(get_db),
):
    """Find calculations for a species entry, filtered by type and level of theory.

    Use ``include=geometry`` to expand the final optimized geometry inline.

    Use ``selection`` to reduce to a single result:
    - ``all`` (default): return all matches.
    - ``lowest_energy``: lowest electronic energy, tie-break: converged → newest → lowest ID.
    - ``latest``: most recently created.
    - ``earliest``: oldest.
    """
    if selection not in SELECTION_MODES:
        raise NotFoundError(f"Unknown selection mode: {selection!r}. Valid: {sorted(SELECTION_MODES)}")

    inputs: dict[str, Any] = {"species_entry_id": species_entry_id}
    if type:
        inputs["type"] = type
    if method:
        inputs["method"] = method
    if basis:
        inputs["basis"] = basis
    if selection != "all":
        inputs["selection"] = selection
    query_echo = LookupQuery(kind="calculations", inputs=inputs)
    mb = _MatchBuilder()

    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")

    stmt = select(Calculation).where(Calculation.species_entry_id == species_entry_id)
    if type is not None:
        stmt = stmt.where(Calculation.type == type)
    stmt = stmt.outerjoin(LevelOfTheory, Calculation.lot_id == LevelOfTheory.id)
    if method is not None:
        stmt = stmt.where(LevelOfTheory.method == method.lower())
    if basis is not None:
        stmt = stmt.where(LevelOfTheory.basis == basis.lower())

    calcs = list(session.scalars(stmt.order_by(Calculation.id)).all())
    calcs = _apply_selection(calcs, selection, session, mb)

    results: list[LookupResultItem] = []

    if not calcs:
        mb.add(CALCULATION_NONE, "no matching calculations found")
    else:
        mb.add(CALCULATION_EXISTS, f"{len(calcs)} calculation(s) found")
        for calc in calcs:
            lot = session.get(LevelOfTheory, calc.lot_id) if calc.lot_id else None
            lot_status = _lot_match(lot, method, basis, mb)

            results.append(LookupResultItem(
                resource_type="calculation",
                id=calc.id,
                links=ResourceLink(
                    self=f"/api/v1/calculations/{calc.id}",
                    owner=f"/api/v1/species-entries/{species_entry_id}",
                ),
                summary=_calc_summary(
                    calc, lot, session, lot_status,
                    include_geometry="geometry" in include,
                ),
            ))

    return LookupResponse(query=query_echo, match=mb.build(), results=results)


# ---------------------------------------------------------------------------
# 3. Result lookup: /lookup/thermo
# ---------------------------------------------------------------------------


@router.get("/thermo", response_model=LookupResponse)
def lookup_thermo(
    species_entry_id: int = Query(..., description="Species entry to search"),
    session: Session = Depends(get_db),
):
    """Find thermo records for a species entry."""
    query_echo = LookupQuery(
        kind="thermo",
        inputs={"species_entry_id": species_entry_id},
    )
    mb = _MatchBuilder()

    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")

    rows = session.scalars(
        select(Thermo)
        .where(Thermo.species_entry_id == species_entry_id)
        .order_by(Thermo.id)
    ).all()

    results: list[LookupResultItem] = []

    if not rows:
        mb.add(THERMO_NONE, "no thermo records found for this species entry")
    else:
        mb.add(THERMO_EXISTS, f"{len(rows)} thermo record(s) found")
        for thermo in rows:
            summary: dict[str, Any] = {
                "scientific_origin": (
                    thermo.scientific_origin.value
                    if hasattr(thermo.scientific_origin, "value")
                    else str(thermo.scientific_origin)
                ),
            }
            if thermo.h298_kj_mol is not None:
                summary["h298_kj_mol"] = thermo.h298_kj_mol
            if thermo.s298_j_mol_k is not None:
                summary["s298_j_mol_k"] = thermo.s298_j_mol_k
            if thermo.tmin_k is not None:
                summary["tmin_k"] = thermo.tmin_k
            if thermo.tmax_k is not None:
                summary["tmax_k"] = thermo.tmax_k

            results.append(LookupResultItem(
                resource_type="thermo",
                id=thermo.id,
                links=ResourceLink(
                    self=f"/api/v1/thermo/{thermo.id}",
                    owner=f"/api/v1/species-entries/{species_entry_id}",
                ),
                summary=summary,
            ))

    return LookupResponse(query=query_echo, match=mb.build(), results=results)


# ---------------------------------------------------------------------------
# 4. Composed lookup: /lookup/species-calculation
# ---------------------------------------------------------------------------


@router.get("/species-calculation", response_model=LookupResponse)
def lookup_species_calculation(
    smiles: str = Query(..., description="SMILES string"),
    charge: int = Query(..., description="Formal charge"),
    multiplicity: int = Query(..., ge=1, description="Spin multiplicity"),
    type: str = Query(..., description="Calculation type: opt, freq, sp"),
    method: str = Query(..., description="Level of theory method"),
    basis: str | None = Query(None, description="Basis set"),
    include: list[str] = Query(default=[], description="Optional expansions: geometry"),
    selection: str = Query("all", description="Selection mode: all, lowest_energy, latest, earliest"),
    session: Session = Depends(get_db),
):
    """One-shot lookup: resolve species identity and find matching calculations.

    Avoids a two-request round trip for the common question:
    "do you have an opt for [H][H] at wb97xd/def2tzvp?"

    Use ``include=geometry`` to expand the final optimized geometry inline.

    Use ``selection`` to reduce to a single result:
    - ``all`` (default): return all matches.
    - ``lowest_energy``: lowest electronic energy, tie-break:
      converged → newest → lowest ID.
    - ``latest``: most recently created.
    - ``earliest``: oldest.
    """
    if selection not in SELECTION_MODES:
        raise NotFoundError(f"Unknown selection mode: {selection!r}. Valid: {sorted(SELECTION_MODES)}")

    inputs: dict[str, Any] = {
        "smiles": smiles, "charge": charge, "multiplicity": multiplicity,
        "type": type, "method": method,
    }
    if basis:
        inputs["basis"] = basis
    if selection != "all":
        inputs["selection"] = selection
    query_echo = LookupQuery(kind="species_calculation", inputs=inputs)
    mb = _MatchBuilder()

    # Step 1: resolve species identity
    payload = SpeciesEntryIdentityPayload(
        smiles=smiles, charge=charge, multiplicity=multiplicity,
    )
    try:
        _canonical_smiles, inchi_key = canonical_species_identity(payload)
    except ValueError as e:
        mb.add(SPECIES_IDENTITY_NONE, str(e))
        return LookupResponse(query=query_echo, match=mb.build())

    species = session.scalar(
        select(Species).where(Species.inchi_key == inchi_key)
    )
    if species is None:
        mb.add(SPECIES_IDENTITY_NONE, "species identity not found")
        return LookupResponse(query=query_echo, match=mb.build())

    mb.add(SPECIES_IDENTITY_EXACT, "species identity matched exactly")

    results: list[LookupResultItem] = []

    results.append(LookupResultItem(
        resource_type="species",
        id=species.id,
        links=ResourceLink(self=f"/api/v1/species/{species.id}"),
        summary={"smiles": species.smiles, "inchi_key": species.inchi_key},
    ))

    # Step 2: find species entry
    entry = session.scalar(
        select(SpeciesEntry)
        .where(SpeciesEntry.species_id == species.id)
        .order_by(SpeciesEntry.id)
        .limit(1)
    )
    if entry is None:
        mb.add(SPECIES_ENTRY_NONE, "no species entries found")
        return LookupResponse(query=query_echo, match=mb.build(), results=results)

    mb.add(SPECIES_ENTRY_EXISTS, "species entry exists")
    results.append(LookupResultItem(
        resource_type="species_entry",
        id=entry.id,
        links=ResourceLink(
            self=f"/api/v1/species-entries/{entry.id}",
            owner=f"/api/v1/species/{species.id}",
        ),
        summary={
            "kind": entry.kind.value if hasattr(entry.kind, "value") else str(entry.kind),
        },
    ))

    # Step 3: find matching calculations
    stmt = (
        select(Calculation)
        .where(Calculation.species_entry_id == entry.id)
        .where(Calculation.type == type)
        .outerjoin(LevelOfTheory, Calculation.lot_id == LevelOfTheory.id)
        .where(LevelOfTheory.method == method.lower())
    )
    if basis is not None:
        stmt = stmt.where(LevelOfTheory.basis == basis.lower())

    calcs = list(session.scalars(stmt.order_by(Calculation.id)).all())
    calcs = _apply_selection(calcs, selection, session, mb)

    if not calcs:
        mb.add(CALCULATION_NONE, "species exists but no matching calculations at this LOT")
    else:
        mb.add(CALCULATION_EXISTS, f"{len(calcs)} calculation(s) found")
        for calc in calcs:
            lot = session.get(LevelOfTheory, calc.lot_id) if calc.lot_id else None
            lot_status = _lot_match(lot, method, basis, mb)

            results.append(LookupResultItem(
                resource_type="calculation",
                id=calc.id,
                links=ResourceLink(
                    self=f"/api/v1/calculations/{calc.id}",
                    owner=f"/api/v1/species-entries/{entry.id}",
                ),
                summary=_calc_summary(
                    calc, lot, session, lot_status,
                    include_geometry="geometry" in include,
                ),
            ))

    return LookupResponse(query=query_echo, match=mb.build(), results=results)


# ---------------------------------------------------------------------------
# 5. Identity lookup: /lookup/reaction
# ---------------------------------------------------------------------------


@router.get("/reaction", response_model=LookupResponse)
def lookup_reaction(
    reactants: list[str] = Query(..., description="Reactant SMILES"),
    products: list[str] = Query(..., description="Product SMILES"),
    reactant_charges: list[int] = Query(None, description="Reactant charges (default: 0)"),
    product_charges: list[int] = Query(None, description="Product charges (default: 0)"),
    reactant_multiplicities: list[int] = Query(None, description="Reactant multiplicities"),
    product_multiplicities: list[int] = Query(None, description="Product multiplicities"),
    reversible: bool = Query(True, description="Whether the reaction is reversible"),
    session: Session = Depends(get_db),
):
    """Resolve a reaction by reactant/product chemical identities.

    Contract: this endpoint resolves to ``reaction_entry`` level, not just
    ``chem_reaction`` identity.  Each returned reaction_entry includes its
    resolved participant structure (species_entry IDs, SMILES, role, order)
    via ``reaction_entry_structure_participant``.
    """
    r_charges = reactant_charges or [0] * len(reactants)
    p_charges = product_charges or [0] * len(products)
    r_mults = reactant_multiplicities or [1] * len(reactants)
    p_mults = product_multiplicities or [1] * len(products)

    query_echo = LookupQuery(
        kind="reaction",
        inputs={"reactants": reactants, "products": products, "reversible": reversible},
    )
    mb = _MatchBuilder()

    chem_rxn, results = _resolve_reaction(
        reactants, products, r_charges, p_charges, r_mults, p_mults,
        reversible, session, mb,
    )
    if chem_rxn is None:
        return LookupResponse(query=query_echo, match=mb.build())

    entries = session.scalars(
        select(ReactionEntry).where(ReactionEntry.reaction_id == chem_rxn.id)
    ).all()

    if not entries:
        mb.add(REACTION_ENTRY_NONE, "reaction identity exists but no entries")
    else:
        mb.add(REACTION_ENTRY_EXISTS, f"{len(entries)} reaction entry(ies)")

    for entry in entries:
        results.append(_reaction_entry_result(entry, chem_rxn.id, session))

    return LookupResponse(query=query_echo, match=mb.build(), results=results)


# ---------------------------------------------------------------------------
# 6. Result lookup: /lookup/kinetics
# ---------------------------------------------------------------------------


@router.get("/kinetics", response_model=LookupResponse)
def lookup_kinetics(
    reaction_entry_id: int = Query(..., description="Reaction entry to search"),
    model_kind: str | None = Query(None, description="Kinetics model kind filter"),
    session: Session = Depends(get_db),
):
    """Find kinetics records for a reaction entry."""
    inputs: dict[str, Any] = {"reaction_entry_id": reaction_entry_id}
    if model_kind:
        inputs["model_kind"] = model_kind
    query_echo = LookupQuery(kind="kinetics", inputs=inputs)
    mb = _MatchBuilder()

    entry = session.get(ReactionEntry, reaction_entry_id)
    if entry is None:
        raise NotFoundError("ReactionEntry not found")

    stmt = select(Kinetics).where(Kinetics.reaction_entry_id == reaction_entry_id)
    if model_kind:
        stmt = stmt.where(Kinetics.model_kind == model_kind)

    rows = session.scalars(stmt.order_by(Kinetics.id)).all()
    results: list[LookupResultItem] = []

    if not rows:
        mb.add(KINETICS_NONE, "no kinetics records found")
    else:
        mb.add(KINETICS_EXISTS, f"{len(rows)} kinetics record(s) found")
        for kin in rows:
            results.append(LookupResultItem(
                resource_type="kinetics",
                id=kin.id,
                links=ResourceLink(
                    self=f"/api/v1/kinetics/{kin.id}",
                    owner=f"/api/v1/reaction-entries/{reaction_entry_id}",
                ),
                summary=_kinetics_summary(kin),
            ))

    return LookupResponse(query=query_echo, match=mb.build(), results=results)


# ---------------------------------------------------------------------------
# 7. Composed lookup: /lookup/reaction-kinetics
# ---------------------------------------------------------------------------


@router.get("/reaction-kinetics", response_model=LookupResponse)
def lookup_reaction_kinetics(
    reactants: list[str] = Query(..., description="Reactant SMILES"),
    products: list[str] = Query(..., description="Product SMILES"),
    reactant_charges: list[int] = Query(None, description="Reactant charges (default: 0)"),
    product_charges: list[int] = Query(None, description="Product charges (default: 0)"),
    reactant_multiplicities: list[int] = Query(None, description="Reactant multiplicities"),
    product_multiplicities: list[int] = Query(None, description="Product multiplicities"),
    reversible: bool = Query(True, description="Whether the reaction is reversible"),
    model_kind: str | None = Query(None, description="Kinetics model kind filter"),
    session: Session = Depends(get_db),
):
    """One-shot: resolve reaction identity and find kinetics."""
    r_charges = reactant_charges or [0] * len(reactants)
    p_charges = product_charges or [0] * len(products)
    r_mults = reactant_multiplicities or [1] * len(reactants)
    p_mults = product_multiplicities or [1] * len(products)

    inputs: dict[str, Any] = {
        "reactants": reactants, "products": products, "reversible": reversible,
    }
    if model_kind:
        inputs["model_kind"] = model_kind
    query_echo = LookupQuery(kind="reaction_kinetics", inputs=inputs)
    mb = _MatchBuilder()

    chem_rxn, results = _resolve_reaction(
        reactants, products, r_charges, p_charges, r_mults, p_mults,
        reversible, session, mb,
    )
    if chem_rxn is None:
        return LookupResponse(query=query_echo, match=mb.build())

    entries = session.scalars(
        select(ReactionEntry).where(ReactionEntry.reaction_id == chem_rxn.id)
    ).all()

    if not entries:
        mb.add(REACTION_ENTRY_NONE, "reaction exists but no entries")
        return LookupResponse(query=query_echo, match=mb.build(), results=results)

    mb.add(REACTION_ENTRY_EXISTS, f"{len(entries)} reaction entry(ies)")

    for entry in entries:
        results.append(_reaction_entry_result(entry, chem_rxn.id, session))

    all_kinetics: list[Kinetics] = []
    for entry in entries:
        stmt = select(Kinetics).where(Kinetics.reaction_entry_id == entry.id)
        if model_kind:
            stmt = stmt.where(Kinetics.model_kind == model_kind)
        all_kinetics.extend(session.scalars(stmt).all())

    if not all_kinetics:
        mb.add(KINETICS_NONE, "reaction exists but no kinetics records found")
    else:
        mb.add(KINETICS_EXISTS, f"{len(all_kinetics)} kinetics record(s) found")
        for kin in all_kinetics:
            results.append(LookupResultItem(
                resource_type="kinetics",
                id=kin.id,
                links=ResourceLink(
                    self=f"/api/v1/kinetics/{kin.id}",
                    owner=f"/api/v1/reaction-entries/{kin.reaction_entry_id}",
                ),
                summary=_kinetics_summary(kin),
            ))

    return LookupResponse(query=query_echo, match=mb.build(), results=results)


# ---------------------------------------------------------------------------
# 8. Identity lookup: /lookup/geometry
# ---------------------------------------------------------------------------


@router.get("/geometry", response_model=LookupResponse)
def lookup_geometry(
    geom_hash: str = Query(..., description="Stored geometry identity hash"),
    session: Session = Depends(get_db),
):
    """Discover whether a geometry already exists, by exact hash.

    First-pass matching semantics: exact hash only. Optional geometry-payload
    canonicalization may be added later; see ``lookup-expansion-spec.md``.
    """
    query_echo = LookupQuery(kind="geometry", inputs={"geom_hash": geom_hash})
    mb = _MatchBuilder()

    geom = session.scalar(
        select(Geometry).where(Geometry.geom_hash == geom_hash)
    )
    if geom is None:
        mb.add(GEOMETRY_IDENTITY_NONE, "no geometry with this hash exists")
        return LookupResponse(query=query_echo, match=mb.build())

    mb.add(GEOMETRY_IDENTITY_EXACT, "geometry identity matched exactly by hash")

    calc_count = session.scalar(
        select(func.count(CalculationOutputGeometry.calculation_id))
        .where(CalculationOutputGeometry.geometry_id == geom.id)
    ) or 0

    result = LookupResultItem(
        resource_type="geometry",
        id=geom.id,
        links=ResourceLink(self=f"/api/v1/geometries/{geom.id}"),
        summary={
            "geom_hash": geom.geom_hash,
            "natoms": geom.natoms,
            "calculation_output_count": calc_count,
        },
    )
    return LookupResponse(query=query_echo, match=mb.build(), results=[result])


# ---------------------------------------------------------------------------
# 9. Result lookup: /lookup/statmech
# ---------------------------------------------------------------------------


def _statmech_summary(row: Statmech) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "scientific_origin": (
            row.scientific_origin.value
            if hasattr(row.scientific_origin, "value")
            else str(row.scientific_origin)
        ),
    }
    if row.statmech_treatment is not None:
        summary["statmech_treatment"] = (
            row.statmech_treatment.value
            if hasattr(row.statmech_treatment, "value")
            else str(row.statmech_treatment)
        )
    if row.rigid_rotor_kind is not None:
        summary["rigid_rotor_kind"] = (
            row.rigid_rotor_kind.value
            if hasattr(row.rigid_rotor_kind, "value")
            else str(row.rigid_rotor_kind)
        )
    if row.point_group is not None:
        summary["point_group"] = row.point_group
    if row.is_linear is not None:
        summary["is_linear"] = row.is_linear
    if row.external_symmetry is not None:
        summary["external_symmetry"] = row.external_symmetry
    if row.software_release_id is not None:
        summary["software_release_id"] = row.software_release_id
    if row.workflow_tool_release_id is not None:
        summary["workflow_tool_release_id"] = row.workflow_tool_release_id
    if row.literature_id is not None:
        summary["literature_id"] = row.literature_id
    return summary


@router.get("/statmech", response_model=LookupResponse)
def lookup_statmech(
    species_entry_id: int = Query(..., description="Species entry to search"),
    session: Session = Depends(get_db),
):
    """Discover statmech records attached to a species entry.

    Append-only: multiple records for the same species entry are all
    returned, not collapsed into one ``preferred`` row.
    """
    query_echo = LookupQuery(
        kind="statmech",
        inputs={"species_entry_id": species_entry_id},
    )
    mb = _MatchBuilder()

    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")

    rows = session.scalars(
        select(Statmech)
        .where(Statmech.species_entry_id == species_entry_id)
        .order_by(Statmech.id)
    ).all()

    results: list[LookupResultItem] = []

    if not rows:
        mb.add(STATMECH_NONE, "no statmech records found for this species entry")
    else:
        mb.add(STATMECH_EXISTS, f"{len(rows)} statmech record(s) found")
        for row in rows:
            results.append(LookupResultItem(
                resource_type="statmech",
                id=row.id,
                links=ResourceLink(
                    self=f"/api/v1/statmech/{row.id}",
                    owner=f"/api/v1/species-entries/{species_entry_id}",
                ),
                summary=_statmech_summary(row),
            ))

    return LookupResponse(query=query_echo, match=mb.build(), results=results)


# ---------------------------------------------------------------------------
# 10. Result lookup: /lookup/transport
# ---------------------------------------------------------------------------


def _transport_summary(row: Transport) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "scientific_origin": (
            row.scientific_origin.value
            if hasattr(row.scientific_origin, "value")
            else str(row.scientific_origin)
        ),
    }
    if row.sigma_angstrom is not None:
        summary["sigma_angstrom"] = row.sigma_angstrom
    if row.epsilon_over_k_k is not None:
        summary["epsilon_over_k_k"] = row.epsilon_over_k_k
    if row.dipole_debye is not None:
        summary["dipole_debye"] = row.dipole_debye
    if row.polarizability_angstrom3 is not None:
        summary["polarizability_angstrom3"] = row.polarizability_angstrom3
    if row.rotational_relaxation is not None:
        summary["rotational_relaxation"] = row.rotational_relaxation
    if row.software_release_id is not None:
        summary["software_release_id"] = row.software_release_id
    if row.workflow_tool_release_id is not None:
        summary["workflow_tool_release_id"] = row.workflow_tool_release_id
    if row.literature_id is not None:
        summary["literature_id"] = row.literature_id
    return summary


@router.get("/transport", response_model=LookupResponse)
def lookup_transport(
    species_entry_id: int = Query(..., description="Species entry to search"),
    session: Session = Depends(get_db),
):
    """Discover transport records attached to a species entry.

    Append-only: multiple records for the same species entry are all
    returned, not collapsed into one ``preferred`` row.
    """
    query_echo = LookupQuery(
        kind="transport",
        inputs={"species_entry_id": species_entry_id},
    )
    mb = _MatchBuilder()

    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")

    rows = session.scalars(
        select(Transport)
        .where(Transport.species_entry_id == species_entry_id)
        .order_by(Transport.id)
    ).all()

    results: list[LookupResultItem] = []

    if not rows:
        mb.add(TRANSPORT_NONE, "no transport records found for this species entry")
    else:
        mb.add(TRANSPORT_EXISTS, f"{len(rows)} transport record(s) found")
        for row in rows:
            results.append(LookupResultItem(
                resource_type="transport",
                id=row.id,
                links=ResourceLink(
                    self=f"/api/v1/transport/{row.id}",
                    owner=f"/api/v1/species-entries/{species_entry_id}",
                ),
                summary=_transport_summary(row),
            ))

    return LookupResponse(query=query_echo, match=mb.build(), results=results)


# ---------------------------------------------------------------------------
# 11. Membership lookup: /lookup/network
# ---------------------------------------------------------------------------


@router.get("/network", response_model=LookupResponse)
def lookup_network(
    species_entry_ids: list[int] = Query(
        ...,
        description=(
            "Species-entry IDs participating in the network. Matching is "
            "contains-all: returned networks contain every requested ID."
        ),
    ),
    session: Session = Depends(get_db),
):
    """Discover networks by participating species-entry membership.

    First-pass matching semantics: **contains-all**. A network is returned
    only if it lists every requested ``species_entry_id`` in
    ``network_species`` (any role). This matches how network identity is
    experienced scientifically and avoids the noisiness of contains-any.
    """
    inputs: dict[str, Any] = {"species_entry_ids": list(species_entry_ids)}
    query_echo = LookupQuery(kind="network", inputs=inputs)
    mb = _MatchBuilder()

    requested = list(dict.fromkeys(species_entry_ids))
    missing: list[int] = []
    for se_id in requested:
        if session.get(SpeciesEntry, se_id) is None:
            missing.append(se_id)
    if missing:
        mb.add(
            SPECIES_ENTRY_NOT_FOUND,
            f"species_entry_id(s) not found: {missing}",
        )
        return LookupResponse(query=query_echo, match=mb.build())

    mb.add(
        NETWORK_MEMBERSHIP_CONTAINS_ALL,
        f"matching networks must contain all of {requested}",
    )

    # contains-all: group by network_id, count distinct requested species-entries
    # present, keep networks whose count equals len(requested).
    n_requested = len(requested)
    network_id_rows = session.execute(
        select(NetworkSpecies.network_id)
        .where(NetworkSpecies.species_entry_id.in_(requested))
        .group_by(NetworkSpecies.network_id)
        .having(
            func.count(func.distinct(NetworkSpecies.species_entry_id)) == n_requested
        )
        .order_by(NetworkSpecies.network_id)
    ).all()
    network_ids = [row[0] for row in network_id_rows]

    results: list[LookupResultItem] = []

    if not network_ids:
        mb.add(NETWORK_NONE, "no networks contain all requested species entries")
        return LookupResponse(query=query_echo, match=mb.build(), results=results)

    mb.add(NETWORK_EXISTS, f"{len(network_ids)} network(s) contain all requested species")

    for network_id in network_ids:
        net = session.get(Network, network_id)
        if net is None:
            continue
        species_count = session.scalar(
            select(func.count(func.distinct(NetworkSpecies.species_entry_id)))
            .where(NetworkSpecies.network_id == network_id)
        ) or 0
        summary: dict[str, Any] = {
            "name": net.name,
            "species_count": species_count,
            "matched_species_entry_ids": requested,
        }
        if net.description is not None:
            summary["description"] = net.description
        results.append(LookupResultItem(
            resource_type="network",
            id=net.id,
            links=ResourceLink(self=f"/api/v1/networks/{net.id}"),
            summary=summary,
        ))

    return LookupResponse(query=query_echo, match=mb.build(), results=results)
