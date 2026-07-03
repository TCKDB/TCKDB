"""Hosted abuse-control caps for public scientific reads.

Covers the limit/offset/geometry/full caps defined in
``docs/specs/public_read_abuse_controls.md``.
"""

from __future__ import annotations

from app.api.config import settings

# ---------------------------------------------------------------------------
# Pagination caps
# ---------------------------------------------------------------------------


def test_limit_above_max_returns_422(client):
    resp = client.get(
        "/api/v1/scientific/reactions/search"
        "?reactants=A&products=B"
        f"&limit={settings.public_max_limit + 1}"
    )
    # FastAPI rejects at the Query(le=200) layer before the service code
    # runs, so the response is a Pydantic validation error.
    assert resp.status_code == 422


def test_offset_above_max_returns_422(client, monkeypatch):
    """When offset exceeds the configured cap, the service returns a stable code."""
    monkeypatch.setattr(settings, "public_max_offset", 5)
    resp = client.get(
        "/api/v1/scientific/reactions/search?reactants=A&products=B&offset=6"
    )
    assert resp.status_code == 422
    assert "offset_too_large" in resp.json()["detail"]


def test_reaction_search_without_meaningful_filter_returns_422(client):
    resp = client.get("/api/v1/scientific/reactions/search")
    assert resp.status_code == 422
    assert "missing_reaction_search_filter" in resp.json()["detail"]


def test_reaction_search_with_reactants_still_works(client, db_session):
    """The filter guard must not break legitimate participant-only lookups."""
    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_reaction_entry,
        make_species,
        make_species_entry,
        next_inchi_key,
    )

    rs = make_species(db_session, smiles="QC1", inchi_key=next_inchi_key("QC1"))
    ps = make_species(db_session, smiles="QC2", inchi_key=next_inchi_key("QC2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )

    resp = client.get(
        "/api/v1/scientific/reactions/search?reactants=QC1&products=QC2"
    )
    assert resp.status_code == 200


def test_reaction_search_with_reaction_entry_ref_still_works(client, db_session):
    """An exact ref lookup with no participant filter must still resolve."""
    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_reaction_entry,
        make_species,
        make_species_entry,
        next_inchi_key,
    )

    rs = make_species(db_session, smiles="QR1", inchi_key=next_inchi_key("QR1"))
    ps = make_species(db_session, smiles="QR2", inchi_key=next_inchi_key("QR2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )

    resp = client.get(
        "/api/v1/scientific/reactions/search"
        f"?reaction_entry_ref={entry.public_ref}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["records"]) == 1


# ---------------------------------------------------------------------------
# Geometry cap
# ---------------------------------------------------------------------------


def test_geometry_above_atom_cap_returns_geometry_too_large(
    client, db_session, monkeypatch
):
    """Pre-existing geometries above the public cap respond with 422."""
    from tests.services.scientific_read._factories import make_geometry

    geom = make_geometry(db_session, natoms=12, xyz_text="placeholder")
    monkeypatch.setattr(settings, "max_geometry_atoms_public", 5)

    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    assert resp.status_code == 422
    assert "geometry_too_large" in resp.json()["detail"]


def test_geometry_within_cap_returns_200(client, db_session, monkeypatch):
    """Sanity check: small geometries are unaffected by the cap."""
    from tests.services.scientific_read._factories import make_geometry

    geom = make_geometry(db_session, natoms=2, xyz_text=None)
    monkeypatch.setattr(settings, "max_geometry_atoms_public", 5)

    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /full expansion cap
# ---------------------------------------------------------------------------


def _seed_reaction_entry_with_n_calculations(db_session, *, label: str, n: int):
    """Set up a reaction entry whose TS has *n* calculations attached.

    Each calculation lands on the entry's TS so the /full builder's
    ``calculations`` section returns *n* rows.
    """
    from app.db.models.calculation import Calculation
    from app.db.models.common import CalculationType
    from app.db.models.transition_state import (
        TransitionState,
        TransitionStateEntry,
    )
    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_reaction_entry,
        make_species,
        make_species_entry,
        next_inchi_key,
    )

    rs = make_species(db_session, smiles=f"{label}A", inchi_key=next_inchi_key(f"{label}A"))
    ps = make_species(db_session, smiles=f"{label}B", inchi_key=next_inchi_key(f"{label}B"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )

    ts = TransitionState(reaction_entry_id=entry.id)
    db_session.add(ts)
    db_session.flush()
    tse = TransitionStateEntry(
        transition_state_id=ts.id, charge=0, multiplicity=1
    )
    db_session.add(tse)
    db_session.flush()
    for _ in range(n):
        db_session.add(
            Calculation(
                type=CalculationType.opt,
                transition_state_entry_id=tse.id,
            )
        )
    db_session.flush()
    return entry


def test_full_expansion_cap_returns_query_too_expensive(
    client, db_session, monkeypatch
):
    """When ``include=calculations`` expands past the cap → 422."""
    entry = _seed_reaction_entry_with_n_calculations(db_session, label="F", n=3)
    monkeypatch.setattr(settings, "max_full_calculations_public", 1)

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=calculations"
    )
    assert resp.status_code == 422
    assert "query_too_expensive" in resp.json()["detail"]


def test_include_all_does_not_bypass_caps(client, db_session, monkeypatch):
    """``include=all`` is subject to the same cap as an explicit include."""
    entry = _seed_reaction_entry_with_n_calculations(db_session, label="I", n=3)
    monkeypatch.setattr(settings, "max_full_calculations_public", 1)

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=all"
    )
    assert resp.status_code == 422
    assert "query_too_expensive" in resp.json()["detail"]
