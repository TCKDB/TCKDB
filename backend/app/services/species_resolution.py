from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from rdkit import Chem
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.api.error_contract import CodedValueError
from app.chemistry.geometry import parse_xyz, resolve_element_symbol
from app.chemistry.species import (
    canonical_isotope_key,
    canonical_species_identity,
    classify_stereo_kind,
    derive_stereo_label_from_3d,
    derive_unmapped_smiles,
    element_counts_from_smiles,
    format_element_counts,
    identity_mol_from_smiles,
    is_electron,
    isotope_substitutions,
)
from app.db.models.common import MoleculeKind, StereoKind
from app.db.models.species import Species, SpeciesEntry
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)

#: Raised when a geometry is not made of the atoms its own SMILES declares.
W_SPECIES_GEOMETRY_COMPOSITION_MISMATCH = "species_geometry_composition_mismatch"

#: Raised when the isotope substitutions in a SMILES and in the geometry
#: deposited under it are different multisets.
W_SPECIES_GEOMETRY_ISOTOPE_MISMATCH = "species_geometry_isotope_mismatch"

#: Raised when a deposit's ``molecule_kind`` contradicts the kind already
#: stored for the species its identity resolves to.
W_SPECIES_KIND_CONFLICT = "species_kind_conflict"


def null_safe_equals(column: ColumnElement, value: str | None) -> ColumnElement[bool]:
    """Build a nullable equality predicate for identity lookups.

    :param column: SQLAlchemy column expression to compare.
    :param value: Candidate value, possibly ``None``.
    :returns: ``column IS NULL`` when ``value`` is ``None``, otherwise ``column = value``.
    """

    return column.is_(None) if value is None else column == value


def resolve_species(
    session: Session,
    payload: SpeciesEntryIdentityPayload,
) -> Species:
    """Resolve or create a species row from upload identity data.

    A free electron resolves like anything else — one row, deduped on the same
    ``(smiles, charge, multiplicity)`` identity as every other species — but
    with the graph-derived steps skipped, because it has no graph. There is
    exactly one electron, so every deposit that declares one converges on that
    single row.

    ``kind`` is asserted, not inherited
    ----------------------------------
    ``molecule_kind`` is written when the row is created and is **not** part of
    the identity, so a later deposit of the same
    ``(smiles, charge, multiplicity)`` used to receive the stored row whatever
    kind it declared, and its own claim was dropped without a word. That is not
    a cosmetic loss: :func:`app.services.reaction_resolution.validate_reaction_elemental_balance`
    and its charge-conservation twin decline to judge any reaction containing a
    ``pseudo`` participant, and they read ``species.kind`` — the stored row —
    not the payload. So a ``pseudo`` row whose free-text ``smiles`` happens to
    collide with a real canonical SMILES silently switched mass balance and
    charge conservation off for every subsequent deposit of that molecule, and
    nothing on any surface said so. The reverse direction loses the exemption a
    lumped construct was declared to need.

    Both are contradictions rather than incompleteness — one identity cannot be
    two kinds of thing — so the disagreement is refused here instead of being
    resolved in favour of whichever deposit arrived first.

    :param session: Active SQLAlchemy session.
    :param payload: Upload-facing species-entry identity payload.
    :returns: Existing or newly created ``Species`` row.
    :raises ValueError: If the payload cannot be canonicalized into a valid
        species identity, or declares a ``molecule_kind`` that contradicts the
        kind already stored for that identity.
    """

    canonical_smiles, inchi_key = canonical_species_identity(payload)

    # Derive stereo_kind from molecular graph if not explicitly provided
    stereo_kind = payload.stereo_kind
    if stereo_kind == StereoKind.unspecified:
        if is_electron(payload):
            # No graph, no stereocentres, and nothing that could ever acquire
            # one — not "unspecified pending better data".
            stereo_kind = StereoKind.achiral
        else:
            ident_mol = identity_mol_from_smiles(payload.smiles)
            stereo_kind, _auto_label = classify_stereo_kind(ident_mol)

    # Identity is (canonical_smiles, charge, multiplicity) — see DR-0031.
    # inchi_key is stored for cross-notation search but is NOT the dedup
    # key: it cannot distinguish spin states (singlet vs triplet CH2) and
    # wrongly merges some tautomers (2-pyridone vs 2-hydroxypyridine).
    identity_filter = (
        Species.smiles == canonical_smiles,
        Species.charge == payload.charge,
        Species.multiplicity == payload.multiplicity,
    )
    species = session.scalar(select(Species).where(*identity_filter))
    if species is None:
        try:
            with session.begin_nested():
                species = Species(
                    kind=payload.molecule_kind,
                    smiles=canonical_smiles,
                    inchi_key=inchi_key,
                    charge=payload.charge,
                    multiplicity=payload.multiplicity,
                    stereo_kind=stereo_kind,
                )
                session.add(species)
                session.flush()
        except IntegrityError:
            species = session.scalar(select(Species).where(*identity_filter))

    assert_declared_kind_matches_stored(payload, species)
    return species


def assert_declared_kind_matches_stored(
    payload: SpeciesEntryIdentityPayload,
    species: Species,
) -> None:
    """Refuse a deposit whose ``molecule_kind`` contradicts the stored row.

    Called on every path out of :func:`resolve_species`, including the one that
    just created the row — where it is a no-op by construction, and cheap
    enough that it is not worth a branch to skip.

    :param payload: Upload-facing resolved identity payload.
    :param species: The species row the identity resolved to.
    :raises CodedValueError: If the two disagree about what kind of thing this
        identity is.
    """

    if species.kind == payload.molecule_kind:
        return

    raise CodedValueError(
        W_SPECIES_KIND_CONFLICT,
        f"This deposit declares molecule_kind={payload.molecule_kind.value!r}, "
        f"but the species identity it resolves to (smiles={species.smiles!r}, "
        f"charge={species.charge}, multiplicity={species.multiplicity}) is "
        f"already stored as molecule_kind={species.kind.value!r} "
        "(species_kind_conflict). Species identity is "
        "(smiles, charge, multiplicity), so both deposits address the same row "
        "and only one kind can be true of it. Accepting the stored kind would "
        "discard the declared one in silence, and the two are not "
        "interchangeable: a pseudo-species excuses every reaction it appears "
        "in from elemental balance and charge conservation, so inheriting "
        "pseudo turns those checks off for a molecule nobody exempted, and "
        "inheriting molecule denies a lumped construct the exemption it was "
        "declared to need. Deposit the identity under the kind it really is, "
        "or give the pseudo-species a smiles that no real structure "
        "canonicalises to.",
        context={
            "declared_molecule_kind": payload.molecule_kind.value,
            "stored_molecule_kind": species.kind.value,
            "smiles": species.smiles,
            "charge": species.charge,
            "multiplicity": species.multiplicity,
        },
        message_prefix=False,
    )


CHECK_SPECIES_KIND_MATCHES_STORED_IDENTITY = ScientificCheck(
    group="A structure against its own label",
    sort_key=0,
    code=W_SPECIES_KIND_CONFLICT,
    asserts=(
        "A deposit's ``molecule_kind`` agrees with the kind already stored for "
        "the species identity it resolves to — one identity is one kind of "
        "thing."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional, and load-bearing for two other blocking checks. Species "
        "identity is ``(smiles, charge, multiplicity)`` and does not include "
        "``kind``, so two deposits that disagree about kind address one row and "
        "at most one of them can be right — that is a contradiction, not "
        "incompleteness. It has to block rather than warn because "
        "``validate_reaction_elemental_balance`` and "
        "``validate_reaction_charge_conservation`` read ``species.kind`` from "
        "the stored row: silently inheriting ``pseudo`` would switch mass "
        "balance and charge conservation off for an ordinary molecule, on "
        "every future reaction containing it, with nothing on any surface "
        "recording that it had happened."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            assert_declared_kind_matches_stored,
            note=(
                "Runs on every path through ``resolve_species``, which is the "
                "sole creator of ``species`` rows on every upload route. A row "
                "written directly to the database — the only way a ``pseudo`` "
                "species exists today, since ``canonical_species_identity`` "
                "refuses that kind — bypasses this, as it bypasses every "
                "Python check; what the check guarantees is that no *upload* "
                "silently adopts a kind it did not declare."
            ),
        ),
    ),
    escape_hatch=(
        "None, and deliberately so: the escape hatch would be exactly the "
        "silent inheritance the check exists to remove. A depositor who "
        "genuinely means the stored kind declares it; one who means the other "
        "kind needs a different identity, because the two claims are about the "
        "same row."
    ),
)


def assert_geometry_isotopes_match_identity(
    payload: SpeciesEntryIdentityPayload,
    geometry: GeometryPayload,
) -> None:
    """Reject a deposit whose geometry and identity disagree about isotopes.

    The species entry's isotope identity comes from the SMILES; the per-atom
    masses that a normal-mode analysis needs come from the geometry. If those
    two disagree, one of them is wrong and there is no defensible way to pick
    a winner — a CD3OH identity attached to an all-protium geometry would
    produce frequencies for a molecule nobody deposited.

    Only the *multiset* of substitutions is compared.

    **Known limitation — isotopomers are NOT distinguished.** The check counts
    how many atoms of each element carry which mass number; it does not check
    *which* atoms. An identity of ``[2H]OC`` (CH3-OD) therefore ACCEPTS a
    geometry that labels a methyl hydrogen instead (CH2D-OH): both carry one
    deuterium, so the multisets agree. Those are different molecules with
    different zero-point energies, and nothing downstream will notice.

    This is a false *acceptance*, not a false rejection: the check never
    rejects a correctly-labelled deposit. Closing it needs an atom-level
    correspondence between the SMILES graph and the XYZ ordering, which this
    repository does not have — 3D bond perception fails silently on exactly
    the strained and radical cases where it would matter most, so guessing a
    mapping would trade a visible gap for an invisible one.

    **Authority for masses.** Where the two disagree in a way this check
    cannot see, the *geometry* is authoritative for per-atom masses: a
    normal-mode analysis reads them from ``geometry.isotopes``, and
    ``geometry_atom`` stores them per atom index. The SMILES is authoritative
    only for *identity* — it decides which ``species_entry`` the deposit
    resolves to via ``isotope_key``. A consumer that needs position-resolved
    isotope labelling must read the geometry, never the SMILES.

    The multiset check remains exact for the errors that actually occur in
    practice: wrong count, wrong element, or isotopes declared on one side
    only.

    :param payload: Upload-facing resolved identity payload.
    :param geometry: Upload-facing geometry payload deposited alongside it.
    :raises ValueError: If the isotope substitutions disagree.
    """

    if is_electron(payload):
        # ``[e-]`` is a sentinel, not a SMILES; there are no nuclei to label.
        # The composition check has already refused any geometry attached to
        # an electron, so reaching here with one is not possible.
        return

    from_smiles = isotope_substitutions(payload.smiles)
    from_geometry = parse_xyz(geometry).isotope_substitutions()
    if from_smiles == from_geometry:
        return

    def _render(counts: dict[tuple[str, int], int]) -> str:
        if not counts:
            return "none (all standard isotopes)"
        return ", ".join(
            f"{mass_number}{element}x{count}"
            for (element, mass_number), count in sorted(counts.items())
        )

    raise CodedValueError(
        W_SPECIES_GEOMETRY_ISOTOPE_MISMATCH,
        "Isotope substitution declared in species_entry.smiles does not match "
        "the uploaded geometry. "
        f"smiles={_render(from_smiles)}; geometry.isotopes={_render(from_geometry)}. "
        "Declare the same substitution on both: use SMILES isotope notation "
        "(e.g. [2H]) for identity and geometry.isotopes for the per-atom masses.",
        context={
            "smiles_substitutions": _render(from_smiles),
            "geometry_substitutions": _render(from_geometry),
        },
        message_prefix=False,
    )


CHECK_GEOMETRY_ISOTOPES_MATCH_IDENTITY = ScientificCheck(
    group="A structure against its own label",
    sort_key=2,
    code=W_SPECIES_GEOMETRY_ISOTOPE_MISMATCH,
    asserts=(
        "The multiset of isotopic substitutions declared in a species entry's "
        "SMILES equals the multiset carried by the geometry deposited under it."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. The identity's isotope label and the geometry's "
        "per-atom masses describe the same nuclei; when they disagree one of "
        "them is wrong and there is no defensible way to pick a winner, so a "
        "CD3OH identity on an all-protium geometry would yield frequencies for "
        "a molecule nobody deposited."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            assert_geometry_isotopes_match_identity,
            note=(
                "Runs from ``resolve_species_entry`` only when a geometry is "
                "supplied. The refusal was prose-only until the code above was "
                "attached; a client could not tell it from any other 422, "
                "which is why the entry used to record the gap here."
            ),
        ),
    ),
    escape_hatch=(
        "Deposit no geometry. An explicitly declared *standard* isotope is "
        "dropped before comparison, so ``{1: 1}`` on a hydrogen cannot fork an "
        "identity away from an unlabelled deposit of the same molecule."
    ),
    divergence=(
        "Not a divergence but a documented false *acceptance*, restated here "
        "because a referee will ask: only the multiset is compared, so "
        "isotopomers are not distinguished. An identity of ``[2H]OC`` (CH3-OD) "
        "accepts a geometry labelling a methyl hydrogen instead (CH2D-OH) — "
        "different molecules with different zero-point energies. Closing it "
        "needs an atom-level SMILES-to-XYZ correspondence the repository does "
        "not have. Where the two disagree invisibly, the geometry is "
        "authoritative for masses and the SMILES only for identity."
    ),
)


def assert_geometry_composition_matches_identity(
    payload: SpeciesEntryIdentityPayload,
    geometry: GeometryPayload,
) -> None:
    """Refuse a conformer geometry not made of the atoms its entry declares.

    Nothing checked this before. A species entry declares what it is in
    ``smiles``; the conformer geometry deposited under it is the structure that
    the numbers hanging off that conformer — energies, frequencies, partition
    functions, thermo — are computed from. If the two name different molecules,
    the record is internally contradictory: the identity says methane and the
    coordinates describe methyl, and every consumer that trusts the label gets
    numbers for a molecule nobody deposited.

    Formula agreement between a structure and its own identifier is
    **definitional** under ADR 0008 — no correct calculation can produce a
    geometry that is not made of its own molecule's atoms — so it blocks,
    exactly as :func:`app.services.reaction_resolution.validate_reaction_elemental_balance`
    blocks the same class of contradiction one layer up and
    :func:`~app.services.reaction_resolution.validate_transition_state_composition`
    blocks it for a saddle point.

    This is not a hypothetical. The pressure-dependent test fixtures stored
    geometries with hydrogen omitted entirely — ethyl as three atoms, HO2 as
    two — for as long as they existed, and nothing looked, because the only
    composition check in the codebase compared a reaction's two *sides*
    against each other and never a structure against its own label.

    Which geometries this reaches, and which it does not
    ---------------------------------------------------
    **Conformer geometries only.** This function is called from
    :func:`resolve_species_entry` and its coverage is exactly that of the
    isotope check beside it: the geometry deposited with a species entry and
    the further conformer geometries deposited under the same entry. That
    reaches the computed-species bundle, ``/uploads/conformers``, the
    computed-reaction bundle and the PDep bundle, because all four resolve
    their conformer geometries through this one seam.

    **Calculation geometries are reached by a sibling rule, not by this one.**
    ``CalculationIn.input_geometries`` and ``output_geometries`` are attached
    to a ``calculation`` row and never resolved through
    :func:`resolve_species_entry`, so this function does not see them. Until
    #143 nothing else did either, and benzene coordinates could be attached as
    a calculation geometry under a ``smiles: "C"`` species entry. They are now
    owned by
    :func:`app.services.calculation_geometry_composition.assert_calculation_geometry_composition`,
    which applies the same comparison against whichever subject the
    calculation is filed under — this entry's own formula for a species-owned
    calculation, the reaction's reactant sum for a transition-state-owned one.

    The note that stood here said closing the gap for *output* geometries
    would be wrong because "an optimisation that dissociated is science to
    record". That is true of connectivity and false of composition: every
    calculation type is a map over a fixed set of nuclei, so a dissociated
    optimisation has exactly the atoms it started with. The sibling rule
    therefore covers both directions; see
    ``backend/docs/specs/calculation_geometry_composition.md``.

    What is compared, and what deliberately is not
    ----------------------------------------------
    **Elements, not nuclides.** ``[2H]`` is stored as element ``H`` in the
    canonical form, so counting isotope-resolved would refuse every
    isotopologue. The XYZ element column gets the same treatment from the
    other direction: ``D`` and ``T`` are legal tokens that every major ESS
    emits or accepts, and they are counted as the hydrogen they are (see
    :func:`app.chemistry.geometry.resolve_element_symbol`). Isotope agreement
    is checked separately and exactly by
    :func:`assert_geometry_isotopes_match_identity`.

    **Counts, not positions.** Two structures with the same formula pass, even
    if the connectivity differs — an isomer deposited under the wrong
    identity is not caught here. Catching that needs 3D bond perception, which
    fails silently on strained and radical systems, i.e. exactly where it
    would matter; the same reasoning that stops
    :func:`assert_geometry_isotopes_match_identity` from distinguishing
    isotopomers.

    **Charge is already owned elsewhere.** The SMILES formal charge is
    compared against the declared ``charge`` by
    :func:`app.chemistry.species.canonical_species_identity`, which blocks. Per
    ADR 0008 the blocking tier owns a rule and the others cite it rather than
    re-deriving it, so this function does not re-check charge — a second copy
    could only disagree with the first.

    **Absence does not block.** No geometry, or a SMILES RDKit will not parse,
    is incompleteness rather than contradiction, and gets the tier an absent
    atom map gets. (In practice an unparseable SMILES is already refused
    upstream by the identity canonicalisation; the branch here exists so this
    function is safe to call from anywhere.)

    Pseudo-species are exempt, mirroring
    :func:`~app.services.reaction_resolution.validate_reaction_elemental_balance`:
    a lumped or phenomenological construct has no atom-resolved composition
    for a geometry to agree with.

    A free electron is **not** exempt, and this is the point of separating the
    two kinds. Its composition is not unknown, it is empty: zero atoms. Any
    geometry deposited under an electron therefore contradicts it and is
    refused here, which is what keeps ``molecule_kind: electron`` from becoming
    a second, quieter way to smuggle a structure past this check.

    :param payload: Upload-facing resolved identity payload.
    :param geometry: Upload-facing geometry payload deposited alongside it.
    :raises ValueError: If the geometry's element counts contradict the
        identity's own SMILES.
    """

    if payload.molecule_kind == MoleculeKind.pseudo:
        return

    if is_electron(payload):
        from_smiles: Counter[str] = Counter()
    else:
        try:
            from_smiles = element_counts_from_smiles(payload.smiles)
        except ValueError:
            return

    from_geometry: Counter[str] = Counter(
        resolve_element_symbol(element)
        for element, _x, _y, _z in parse_xyz(geometry).atoms
    )
    if from_smiles == from_geometry:
        return

    geometry_formula = format_element_counts(from_geometry) or "empty"
    identity_formula = (
        format_element_counts(from_smiles)
        # Not reported by scripts/check_runtime_ascii.py: the literal is bound
        # to a local and only later interpolated into the raise below, which
        # is past what a no-dataflow checker can follow. Fixed by hand.
        or "nothing at all -- a free electron has no atoms"
    )
    raise CodedValueError(
        W_SPECIES_GEOMETRY_COMPOSITION_MISMATCH,
        f"Species geometry is {geometry_formula}, but "
        f"species_entry.smiles={payload.smiles!r} is "
        f"{identity_formula} "
        "(species_geometry_composition_mismatch). A deposited structure must "
        "be made of the atoms its own identifier declares, or every number "
        "computed from it describes a different molecule. Hydrogens are "
        "counted explicitly on both sides, and isotope labels are counted as "
        "their element -- SMILES [2H] and the XYZ symbols D and T all count as "
        "H -- so an isotopologue is not a mismatch.",
        context={
            "geometry_formula": geometry_formula,
            "identity_formula": identity_formula,
        },
        message_prefix=False,
    )


CHECK_GEOMETRY_COMPOSITION_MATCHES_IDENTITY = ScientificCheck(
    group="A structure against its own label",
    sort_key=1,
    code="species_geometry_composition_mismatch",
    asserts=(
        "A conformer geometry deposited under a species entry is made of the "
        "atoms that entry's own SMILES declares."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. No correct calculation produces a geometry that is not "
        "made of its own molecule's atoms, so a formula disagreement between a "
        "structure and its own identifier is a contradiction rather than an "
        "expectation — every energy, frequency and partition function "
        "downstream would describe a different molecule under the deposited "
        "label."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            assert_geometry_composition_matches_identity,
            note=(
                "Conformer geometries only, via ``resolve_species_entry``: the "
                "computed-species bundle, ``/uploads/conformers``, the "
                "computed-reaction bundle and the PDep bundle. **Calculation** "
                "input and output geometries are reached by no composition "
                "check on any path — benzene coordinates can still be attached "
                "as a calculation geometry under a ``smiles: \"C\"`` entry. "
                "Closing that for *output* geometries would be wrong (an "
                "optimisation that dissociated is science to record); for "
                "*input* geometries it is an open gap."
            ),
        ),
    ),
    escape_hatch=(
        "Declare ``molecule_kind: pseudo``, which has no atom-resolved "
        "composition to agree with. A free electron is deliberately **not** "
        "exempt — its composition is not unknown but empty, so any geometry "
        "deposited under one is refused, which stops ``electron`` becoming a "
        "quieter way to smuggle a structure past the check. Absence does not "
        "block: no geometry, or a SMILES RDKit will not parse, is "
        "incompleteness."
    ),
)


def resolve_species_entry(
    session: Session,
    payload: SpeciesEntryIdentityPayload,
    *,
    created_by: int | None = None,
    geometry: GeometryPayload | None = None,
    additional_geometries: Sequence[GeometryPayload] = (),
) -> SpeciesEntry:
    """Resolve or create a species-entry row from upload identity data.

    Isotopic resolution is atom-resolved and derived, never uploaded: the
    entry's ``isotope_key`` is computed from the isotope labels carried by
    ``payload.smiles`` (see
    :func:`app.chemistry.species.canonical_isotope_key`). Two deposits that
    differ only in deuteration therefore resolve to different entries under
    one shared species, and two all-standard deposits dedupe onto one entry.

    A free electron (``molecule_kind: electron``) resolves through here too,
    so a reaction can declare one as a participant, but every graph-derived
    column of its entry stays ``NULL`` — it has no molecular graph — and any
    geometry deposited under it is refused, because an electron has no atoms
    for a structure to be made of.

    :param session: Active SQLAlchemy session.
    :param payload: Upload-facing resolved identity payload.
    :param created_by: Optional application user id for new rows.
    :param geometry: Optional geometry deposited with this entry. When given,
        it supplies 3D stereo perception and is cross-checked against the
        identity's element composition and isotope labels.
    :param additional_geometries: Any further geometries deposited under this
        same entry (the second and later conformers). They are composition- and
        isotope-checked too but never used for stereo perception. Checking only
        the first conformer let a deuterated entry store all-protium geometries
        for every other conformer.
    :returns: Existing or newly created ``SpeciesEntry`` row.
    :raises ValueError: If the underlying species identity cannot be
        canonicalized, or a geometry contradicts the declared composition or
        isotopes.
    """

    # Composition first: a geometry made of the wrong atoms is the larger
    # contradiction, and reporting "this is CH3, not CH4" reads better than an
    # isotope-multiset complaint derived from the same broken structure.
    for candidate_geometry in (
        *(() if geometry is None else (geometry,)),
        *additional_geometries,
    ):
        assert_geometry_composition_matches_identity(payload, candidate_geometry)
        assert_geometry_isotopes_match_identity(payload, candidate_geometry)

    species = resolve_species(session, payload)

    # Derive R/S or E/Z label from 3D geometry when available
    xyz_text = geometry.xyz_text if geometry is not None else None
    stereo_label = payload.stereo_label
    if stereo_label is None and species.stereo_kind != StereoKind.achiral and xyz_text:
        stereo_label = derive_stereo_label_from_3d(payload.smiles, xyz_text)

    # A free electron has no molecular graph, so every graph-derived column
    # stays NULL: no isotope key (no nuclei to label), no ``mol`` and no
    # ``unmapped_smiles`` (nothing for the RDKit cartridge to hold, and nothing
    # a structure search should ever return).
    isotope_key = None if is_electron(payload) else canonical_isotope_key(payload.smiles)

    species_entry = session.scalar(
        select(SpeciesEntry).where(
            SpeciesEntry.species_id == species.id,
            SpeciesEntry.kind == payload.species_entry_kind,
            null_safe_equals(SpeciesEntry.stereo_label, stereo_label),
            SpeciesEntry.electronic_state_kind == payload.electronic_state_kind,
            null_safe_equals(
                SpeciesEntry.electronic_state_label,
                payload.electronic_state_label,
            ),
            null_safe_equals(SpeciesEntry.term_symbol, payload.term_symbol),
            null_safe_equals(SpeciesEntry.isotope_key, isotope_key),
        )
    )
    if species_entry is None:
        # Auto-derive unmapped_smiles and mol SMILES for the RDKit cartridge
        unmapped = payload.unmapped_smiles
        mol_smiles: str | None
        if is_electron(payload):
            unmapped = None
            mol_smiles = None
        else:
            if unmapped is None:
                unmapped = derive_unmapped_smiles(payload.smiles)

            mol_smiles = Chem.MolToSmiles(
                identity_mol_from_smiles(payload.smiles), canonical=True
            )

        try:
            with session.begin_nested():
                species_entry = SpeciesEntry(
                    species_id=species.id,
                    kind=payload.species_entry_kind,
                    mol=mol_smiles,
                    unmapped_smiles=unmapped,
                    stereo_label=stereo_label,
                    electronic_state_kind=payload.electronic_state_kind,
                    electronic_state_label=payload.electronic_state_label,
                    term_symbol_raw=payload.term_symbol_raw,
                    term_symbol=payload.term_symbol,
                    isotope_key=isotope_key,
                    created_by=created_by,
                )
                session.add(species_entry)
                session.flush()
        except IntegrityError:
            species_entry = session.scalar(
                select(SpeciesEntry).where(
                    SpeciesEntry.species_id == species.id,
                    SpeciesEntry.kind == payload.species_entry_kind,
                    null_safe_equals(SpeciesEntry.stereo_label, stereo_label),
                    SpeciesEntry.electronic_state_kind == payload.electronic_state_kind,
                    null_safe_equals(
                        SpeciesEntry.electronic_state_label,
                        payload.electronic_state_label,
                    ),
                    null_safe_equals(SpeciesEntry.term_symbol, payload.term_symbol),
                    null_safe_equals(SpeciesEntry.isotope_key, isotope_key),
                )
            )

    return species_entry


def resolve_species_entry_reference(
    session: Session,
    *,
    species_entry_id: int | None = None,
    payload: SpeciesEntryIdentityPayload | None = None,
    created_by: int | None = None,
) -> SpeciesEntry:
    """Resolve a species entry from either an existing id or an identity payload.

    :param session: Active SQLAlchemy session.
    :param species_entry_id: Existing species-entry id to reuse.
    :param payload: Upload-facing resolved identity payload to resolve when no id is supplied.
    :param created_by: Optional application user id for newly created rows.
    :returns: Existing or newly created ``SpeciesEntry`` row.
    :raises ValueError: If the reference is missing, ambiguous, or points at no stored row.
    """

    if (species_entry_id is None) == (payload is None):
        raise ValueError(
            "Provide exactly one of species_entry_id or species_entry payload."
        )

    if species_entry_id is not None:
        species_entry = session.get(SpeciesEntry, species_entry_id)
        if species_entry is None:
            raise ValueError("Unknown species_entry")
        return species_entry

    assert payload is not None
    return resolve_species_entry(session, payload, created_by=created_by)
