"""Identity-hash determinism invariants.

Identity hashes (``geom_hash``, ``lot_hash``, ``stoichiometry_hash``)
underpin deduplication across the whole backend. A silent change to a
canonicalization step can either:

- over-normalize (two scientifically distinct records collapse into one), or
- under-normalize (equivalent records diverge into spurious duplicates).

Both failure modes are invisible to CRUD and schema tests, so they must
be pinned explicitly here.
"""

from __future__ import annotations

from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.refs import LevelOfTheoryRef
from app.services.calculation_resolution import _level_of_theory_hash
from app.services.geometry_resolution import geometry_create_from_payload
from app.services.reaction_resolution import reaction_stoichiometry_hash

# ---------------------------------------------------------------------------
# geom_hash
# ---------------------------------------------------------------------------


_WATER_XYZ = """3

O 0.0000 0.0000 0.0000
H 0.7572 0.5860 0.0000
H -0.7572 0.5860 0.0000
"""

_WATER_XYZ_WHITESPACE_VARIANT = """3

O    0.0      0.0      0.0
H    0.7572   0.5860   0.0
H   -0.7572   0.5860   0.0
"""

_WATER_XYZ_TRAILING_NEWLINES = _WATER_XYZ + "\n\n"


def _geom_hash(xyz_text: str) -> str:
    return geometry_create_from_payload(GeometryPayload(xyz_text=xyz_text)).geom_hash


def test_geom_hash_is_deterministic_for_identical_input() -> None:
    """Repeatedly hashing the same canonical XYZ must give the same hash.

    If this ever fails, canonicalization has acquired nondeterminism
    (e.g. iteration-order dependence) and deduplication becomes unsafe.
    """
    hashes = {_geom_hash(_WATER_XYZ) for _ in range(5)}
    assert len(hashes) == 1


def test_geom_hash_ignores_insignificant_whitespace() -> None:
    """Whitespace-only differences between semantically identical XYZ
    blocks must produce the same ``geom_hash`` because the canonicalizer
    reformats each coordinate to a fixed precision.

    Regression here means two uploads of the same geometry — e.g. from
    different ESS output formats — would be stored as distinct rows.
    """
    h1 = _geom_hash(_WATER_XYZ)
    h2 = _geom_hash(_WATER_XYZ_WHITESPACE_VARIANT)
    h3 = _geom_hash(_WATER_XYZ_TRAILING_NEWLINES)
    assert h1 == h2 == h3


def test_geom_hash_changes_when_an_atom_moves() -> None:
    """A genuinely different geometry must hash differently.

    Protects against over-normalization that would silently merge
    distinct geometries into a single ``Geometry`` row.
    """
    perturbed = _WATER_XYZ.replace("0.7572", "0.7600")
    assert _geom_hash(_WATER_XYZ) != _geom_hash(perturbed)


def test_geom_hash_changes_when_an_element_changes() -> None:
    """Swapping one atom's element for a different element must change
    the hash. This is a different channel of scientific meaning than
    coordinates and must be independently sensitive."""
    altered = _WATER_XYZ.replace("O 0.0000", "S 0.0000", 1)
    assert _geom_hash(_WATER_XYZ) != _geom_hash(altered)


# ---------------------------------------------------------------------------
# lot_hash
# ---------------------------------------------------------------------------


def _lot(**kwargs) -> LevelOfTheoryRef:
    base: dict = {"method": "B3LYP", "basis": "6-31G(d)"}
    base.update(kwargs)
    return LevelOfTheoryRef(**base)


def test_lot_hash_is_deterministic() -> None:
    """Constructing the same level-of-theory twice must give the same hash."""
    h = {_level_of_theory_hash(_lot()) for _ in range(5)}
    assert len(h) == 1


def test_lot_hash_is_independent_of_field_construction_order() -> None:
    """Pydantic builds the ref from kwargs, so construction ordering must
    not affect the canonical JSON payload used by the hasher."""
    a = _level_of_theory_hash(_lot(basis="cc-pVTZ", method="CCSD(T)"))
    b = _level_of_theory_hash(_lot(method="CCSD(T)", basis="cc-pVTZ"))
    assert a == b


def test_lot_hash_treats_none_and_omitted_fields_as_equivalent() -> None:
    """An explicit ``None`` and an omitted optional field must produce
    the same hash. The backend relies on this for dedupe: refactors that
    accidentally start serializing omitted fields differently would split
    equivalent LoT rows into duplicates."""
    a = _level_of_theory_hash(_lot(dispersion=None))
    b = _level_of_theory_hash(_lot())
    assert a == b


def test_lot_hash_distinguishes_spin_treatment() -> None:
    """Restricted-open vs unrestricted of the same method/basis are
    genuinely different levels of theory (DR-0034) and must not collapse."""
    ro = _level_of_theory_hash(_lot(method="CCSD(T)", spin_treatment="restricted_open"))
    u = _level_of_theory_hash(_lot(method="CCSD(T)", spin_treatment="unrestricted"))
    assert ro != u


def test_lot_hash_omitted_spin_equals_unknown() -> None:
    """Omitted spin_treatment folds to 'unknown' in the hash, so a producer
    that leaves it out matches one that says 'unknown' explicitly."""
    a = _level_of_theory_hash(_lot(method="CCSD(T)"))
    b = _level_of_theory_hash(_lot(method="CCSD(T)", spin_treatment="unknown"))
    assert a == b


def test_lot_hash_changes_for_scientifically_different_levels() -> None:
    """A genuinely different LoT (different method or different basis)
    must produce a different hash. Guards against over-normalization that
    would collapse distinct methods into one row."""
    base = _level_of_theory_hash(_lot())
    assert base != _level_of_theory_hash(_lot(method="wB97X-D"))
    assert base != _level_of_theory_hash(_lot(basis="cc-pVDZ"))
    assert base != _level_of_theory_hash(_lot(solvent="water", solvent_model="SMD"))


# ---------------------------------------------------------------------------
# stoichiometry_hash (reaction graph identity)
# ---------------------------------------------------------------------------


def _stoich_hash(**kwargs) -> str:
    base: dict = {
        "reversible": True,
        "reactants": {1: 1, 2: 1},
        "products": {3: 1},
    }
    base.update(kwargs)
    return reaction_stoichiometry_hash(**base)


def test_stoichiometry_hash_is_deterministic() -> None:
    hashes = {_stoich_hash() for _ in range(5)}
    assert len(hashes) == 1


def test_stoichiometry_hash_is_insensitive_to_participant_ordering() -> None:
    """Reactants/products are compressed into ``{species_id: count}`` maps,
    so insertion order into those dicts must not affect the hash. The
    canonicalizer sorts by species id; this pins that guarantee."""
    a = _stoich_hash(reactants={1: 1, 2: 1}, products={3: 1})
    b = _stoich_hash(reactants={2: 1, 1: 1}, products={3: 1})
    assert a == b


def test_stoichiometry_hash_changes_when_reversibility_changes() -> None:
    """Reversibility is part of reaction identity; toggling it must
    produce a different hash."""
    a = _stoich_hash(reversible=True)
    b = _stoich_hash(reversible=False)
    assert a != b


def test_stoichiometry_hash_changes_when_participants_change() -> None:
    """Different species, different stoichiometry, or side-swapped
    reactants/products are all scientifically different reactions."""
    base = _stoich_hash()
    assert base != _stoich_hash(reactants={1: 2, 2: 1})  # count changed
    assert base != _stoich_hash(reactants={1: 1, 4: 1})  # species changed
    assert base != _stoich_hash(
        reactants={3: 1}, products={1: 1, 2: 1},
    )  # swapped sides
