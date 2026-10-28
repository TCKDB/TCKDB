"""Species key is the constitution; stereo lives on the entry (DR-0038), end to end.

Through the production resolution service and ``POST /uploads/computed-species``: every 2-butene
input is one species whose entries are exactly ``{E, Z, ?}``; every contradiction is a 422
``species_stereo_contradiction``; free text is refused; a partial configuration is its own entry;
exact search sees the species and isomeric search sees the entry.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.chemistry.stereo import W_SPECIES_ENTRY_STEREO_UNRESOLVED
from app.db.models.common import StereoResolution
from app.db.models.species import Species
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.species_resolution import resolve_species_entry
from tests.services.test_stereo_descriptor import embed_xyz

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_TRANS = embed_xyz("C/C=C/C")
_CIS = embed_xyz("C/C=C\\C")


def _resolve(db_session, smiles, *, xyz=None, label=None, charge=0, multiplicity=1):
    payload = SpeciesEntryIdentityPayload(
        smiles=smiles, charge=charge, multiplicity=multiplicity, stereo_label=label
    )
    geometry = GeometryPayload(xyz_text=xyz) if xyz else None
    entry = resolve_species_entry(db_session, payload, geometry=geometry)
    db_session.flush()
    return entry


def _bundle(smiles: str, xyz: str, *, label: str | None = None, multiplicity: int = 1) -> dict:
    identity = {"smiles": smiles, "charge": 0, "multiplicity": multiplicity}
    if label is not None:
        identity["stereo_label"] = label
    return {
        "species_entry": identity,
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": xyz},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_result": {"converged": True},
                },
            }
        ],
    }


def _post(client: TestClient, bundle: dict):
    return client.post("/api/v1/uploads/computed-species", json=bundle)


# ---------------------------------------------------------------------------
# One species, three entries
# ---------------------------------------------------------------------------


def test_every_2_butene_input_is_one_species_with_entries_e_z_and_unknown(db_session) -> None:
    resolved = [
        _resolve(db_session, "C/C=C/C"),
        _resolve(db_session, "CC=CC", xyz=_TRANS),
        _resolve(db_session, "C/C=C\\C"),
        _resolve(db_session, "CC=CC", xyz=_CIS),
        _resolve(db_session, "CC=CC"),
        _resolve(db_session, "C\\C=C\\C", xyz=_TRANS),
        _resolve(db_session, "CC=CC", label="Z"),
    ]
    assert len({entry.species_id for entry in resolved}) == 1
    species = db_session.get(Species, resolved[0].species_id)
    assert species.smiles == "CC=CC"
    assert {entry.stereo_descriptor for entry in species.entries} == {"E", "Z", "?"}
    assert len(species.entries) == 3
    assert [e.stereo_descriptor for e in resolved] == ["E", "E", "Z", "Z", "?", "E", "Z"]
    unknown = next(e for e in species.entries if e.stereo_descriptor == "?")
    assert (unknown.stereo_resolution, unknown.stereo_label) == (StereoResolution.unresolved, None)
    assert unknown.stereo_descriptor is not None  # unknown is `?`, never NULL


def test_radical_marks_do_not_fork_the_species(db_session) -> None:
    marked = _resolve(db_session, "[CH2]/C=C/C", multiplicity=2)
    flat = _resolve(db_session, "[CH2]C=CC", multiplicity=2)
    assert marked.species_id == flat.species_id
    assert (marked.stereo_descriptor, flat.stereo_descriptor) == ("E", "?")


def test_label_alone_resolves_an_entry_with_no_geometry_and_flat_smiles(db_session) -> None:
    entry = _resolve(db_session, "CC=CC", label="E")
    assert (entry.stereo_descriptor, entry.stereo_label, entry.stereo_smiles) == ("E", "E", "C/C=C/C")
    assert _resolve(db_session, "C/C=C/C") .id == entry.id


def test_partial_is_its_own_entry_and_a_full_deposit_does_not_merge_into_it(db_session) -> None:
    partial = _resolve(db_session, "C/C=C/C=CC")
    full = _resolve(db_session, "C/C=C/C=C/C")
    assert partial.species_id == full.species_id
    assert partial.id != full.id
    assert (partial.stereo_descriptor, partial.stereo_resolution) == ("E,?", StereoResolution.partial)
    assert (full.stereo_descriptor, full.stereo_resolution) == ("E,E", StereoResolution.resolved)


def test_both_diazene_spellings_with_a_cis_geometry_are_one_entry(db_session) -> None:
    cis = embed_xyz("[H]/N=N\\[H]")
    first = _resolve(db_session, "[H]/N=N\\[H]", xyz=cis)
    second = _resolve(db_session, "N=N", xyz=cis)
    assert first.id == second.id
    assert first.species.smiles == "N=N"


def test_torsional_rotamers_do_not_split_an_entry(db_session) -> None:
    entries = {
        _resolve(db_session, "CCC=CC", xyz=embed_xyz("CC/C=C/C", seed=seed)).id for seed in (3, 11, 29, 77)
    }
    assert len(entries) == 1


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("smiles", "xyz_smiles", "label"),
    [
        ("C/C=C/C", "C/C=C\\C", None),
        ("C[C@@H](O)CC", "C[C@H](O)CC", None),
        ("CC=CC", "C/C=C\\C", "E"),
    ],
    ids=["marks_vs_geometry_ez", "marks_vs_geometry_rs", "label_vs_geometry"],
)
def test_a_contradiction_is_a_422_with_its_code_and_no_row_ids(
    client: TestClient, db_session, smiles: str, xyz_smiles: str, label: str | None
) -> None:
    # Move the key sequences far past anything the detail text could contain by accident, then look for the ids.
    for sequence in ("species_id_seq", "species_entry_id_seq", "geometry_id_seq"):
        db_session.execute(text(f"SELECT setval('{sequence}', 8675309)"))
    known = _resolve(db_session, "CCCCC")
    response = _post(client, _bundle(smiles, embed_xyz(xyz_smiles), label=label))
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "species_stereo_contradiction"
    detail = str(body["detail"])
    assert str(known.id) not in detail and str(known.species_id) not in detail
    assert not re.search(r"\b\d{5,}\b", detail), detail
    assert "id=" not in detail
    # Refused whole: the contradictory deposit left no species behind.
    assert db_session.scalar(select(Species.id).where(Species.smiles.in_(["CC=CC", "CCC(C)O"]))) is None


def test_free_text_label_is_a_422(client: TestClient) -> None:
    response = _post(client, _bundle("CC=CC", _TRANS, label="trans"))
    assert response.status_code == 422
    assert "stereo_label" in response.text


def test_a_label_that_does_not_fit_the_molecule_is_a_coded_422(client: TestClient) -> None:
    response = _post(client, _bundle("CC=CC", _TRANS, label="R"))
    assert response.status_code == 422
    assert response.json()["code"] == "species_stereo_label_invalid"


# ---------------------------------------------------------------------------
# Warning
# ---------------------------------------------------------------------------


def test_an_unresolved_entry_is_accepted_with_a_warning(client: TestClient) -> None:
    unresolved = _post(client, _bundle("[CH]=CC", _linear_propenyl(), multiplicity=2))
    assert unresolved.status_code == 201, unresolved.text
    codes = [w["code"] for w in unresolved.json()["warnings"]]
    assert W_SPECIES_ENTRY_STEREO_UNRESOLVED in codes


def test_a_resolved_entry_carries_no_stereo_warning(client: TestClient) -> None:
    response = _post(client, _bundle("C/C=C/C", _TRANS))
    assert response.status_code == 201, response.text
    assert W_SPECIES_ENTRY_STEREO_UNRESOLVED not in [w["code"] for w in response.json()["warnings"]]


def _linear_propenyl() -> str:
    # H-C1=C2 collinear: the configuration is geometrically undefined, so geometry resolves nothing.
    return """8

C 0.0000 0.0000 0.0000
C 1.3200 0.0000 0.0000
H -1.0800 0.0000 0.0000
C 2.1370 1.2580 0.0000
H 1.8600 -0.9353 0.0000
H 2.6370 2.1580 0.0000
H 2.6370 1.5580 0.9000
H 2.6370 1.5580 -0.9000
"""


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

_SEARCH = "/api/v1/scientific/species/structure-search"


def _seed_2_butene(db_session):
    entries = {
        "E": _resolve(db_session, "C/C=C/C"),
        "Z": _resolve(db_session, "C/C=C\\C"),
        "?": _resolve(db_session, "CC=CC"),
    }
    db_session.flush()
    return entries


def _hits(client: TestClient, **params) -> list[dict]:
    response = client.get(_SEARCH, params={"mode": "exact", "include_rejected": True, **params})
    assert response.status_code == 200, response.text
    return response.json()["records"]


def test_exact_search_for_either_spelling_finds_the_same_species(client: TestClient, db_session) -> None:
    entries = _seed_2_butene(db_session)
    flat = {r["species_entry_ref"] for r in _hits(client, query_smiles="CC=CC")}
    marked = {r["species_entry_ref"] for r in _hits(client, query_smiles="C/C=C/C")}
    assert flat == marked == {entry.public_ref for entry in entries.values()}


def test_isomeric_search_finds_only_the_matching_entry(client: TestClient, db_session) -> None:
    entries = _seed_2_butene(db_session)
    only_e = {r["species_entry_ref"] for r in _hits(client, query_smiles="C/C=C/C", isomeric=True)}
    only_z = {r["species_entry_ref"] for r in _hits(client, query_smiles="C/C=C\\C", isomeric=True)}
    assert only_e == {entries["E"].public_ref}
    assert only_z == {entries["Z"].public_ref}
    assert {r["species_entry_ref"] for r in _hits(client, query_smiles="CC=CC", isomeric=True)} == {
        entries["?"].public_ref
    }


def test_the_entry_label_says_an_unresolved_or_partial_entry_is_so(client: TestClient, db_session) -> None:
    """``species_entry_label`` is built from the descriptor and resolution, not the NULL display label."""
    entries = _seed_2_butene(db_session)
    partial = _resolve(db_session, "C/C=C/C=CC")
    achiral = _resolve(db_session, "CCO")
    db_session.flush()

    def label(ref: str, smiles: str) -> str | None:
        hit = next(r for r in _hits(client, query_smiles=smiles) if r["species_entry_ref"] == ref)
        return hit["species_entry_label"]

    assert label(entries["E"].public_ref, "CC=CC") == "E"
    assert label(entries["Z"].public_ref, "CC=CC") == "Z"
    assert label(entries["?"].public_ref, "CC=CC") == "?[unresolved]"
    assert label(partial.public_ref, "CC=CC=CC") == "E,?[partial]"
    assert label(achiral.public_ref, "CCO") is None


# ---------------------------------------------------------------------------
# Reads say which ``?`` no data can settle
# ---------------------------------------------------------------------------


def test_reads_flag_an_allene_axis_as_not_represented_and_a_plain_unknown_as_unknown(
    client: TestClient, db_session
) -> None:
    """``stereo_not_represented`` lets a reader tell "not yet supplied" from "cannot be stated yet"."""
    allene = _resolve(db_session, "CC=C=CC")
    butene = _resolve(db_session, "CC=CC")
    db_session.flush()

    allene_hit = _hits(client, query_smiles="CC=C=CC")
    assert [r["species_entry_ref"] for r in allene_hit] == [allene.public_ref]
    assert allene_hit[0]["stereo_descriptor"] == "?"
    assert allene_hit[0]["stereo_not_represented"] == ["axial"]

    butene_hit = _hits(client, query_smiles="CC=CC")
    assert [r["species_entry_ref"] for r in butene_hit] == [butene.public_ref]
    assert butene_hit[0]["stereo_not_represented"] == []

    species = client.get(
        "/api/v1/scientific/species/search", params={"species_entry_ref": allene.public_ref}
    ).json()["records"][0]["entries"]
    assert species[0]["stereo_not_represented"] == ["axial"]
