"""Hosted abuse-control caps for public scientific reads.

Covers the limit/offset/geometry/full caps defined in
``docs/specs/public_read_abuse_controls.md``.

What the cap family publishes, and what it must not
---------------------------------------------------
Since 2026-08-18 every cap code is a
``app.api.code_catalogue.Shape.relationship``: a refusal that does not
state the limit cannot be acted on without guessing, and these caps are
settings, so no document a client can read has the right number in it.
The tests below therefore assert the **specific key and its value**, not
that ``context`` is non-empty -- the latter is satisfied by a handler
stuffing in a constant.

The other half is an *absence*, and it is the half nothing else in the
suite would notice if it were lost. The rule on ``Shape`` is:

    Publish the THRESHOLD. Never publish the MEASURED VALUE that crossed
    it, where that value describes the corpus rather than the caller's
    own request.

``query_too_expensive`` is the case in this file. Its measured value is
how many calculation / geometry / artifact rows TCKDB holds for the
requested record, and a caller who can read that off a cheap refusal has
a row-counting oracle -- the enumeration exposure
``docs/specs/public_identifier_policy.md`` rejects integer primary keys
for. So the count is logged, and its absence is asserted **over the
whole body**, not only ``context``: ``detail`` is published too, and an
omission from one and not the other is decorative. Its three siblings
(``export_all_cap_exceeded``, ``ml_export_all_cap_exceeded``,
``composed_search_candidate_limit_exceeded``) are asserted the same way
in ``test_api_untested_refusals_tier_de.py``, next to their own accept
halves.
"""

from __future__ import annotations

import json
import re

from app.api.config import settings
from app.schemas.reads._field_bounds import MAX_SMILES_LENGTH

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


# ---------------------------------------------------------------------------
# The caps now say what the cap is (2026-08-18)
# ---------------------------------------------------------------------------


def test_limit_too_large_publishes_the_cap_and_the_supplied_limit(
    client, monkeypatch
):
    """The whole point of the reclassification: a retryable number.

    ``public_max_limit`` is a *setting*. A deployment that lowers it has
    no document a client can consult, so before this the only way to find
    a legal page size was to bisect. Both values are safe: the cap is
    TCKDB's own configuration and the limit is the caller's own request
    handed back.

    The key and its value are asserted, not merely that ``context`` is
    populated -- a handler that stuffed in a constant would satisfy the
    weaker form.
    """
    monkeypatch.setattr(settings, "public_max_limit", 10)
    resp = client.get("/api/v1/scientific/species/search?smiles=X&limit=50")

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "limit_too_large"
    assert body["context"] == {"limit_max": 10, "limit": 50}


def test_a_limit_inside_the_lowered_cap_still_succeeds(client, monkeypatch):
    """The accept-half, under the same lowered cap.

    Without it the refusal above is equally well satisfied by a service
    that refuses every paginated read.
    """
    monkeypatch.setattr(settings, "public_max_limit", 10)
    resp = client.get("/api/v1/scientific/species/search?smiles=X&limit=10")
    assert resp.status_code == 200, resp.text


def test_offset_too_large_publishes_the_cap_and_the_supplied_offset(
    client, monkeypatch
):
    """The sibling, and the one reachable against shipped settings.

    Lowered here anyway so the asserted cap is a number this test chose,
    which is what makes ``offset_max`` an assertion about the response
    rather than about the configuration.
    """
    monkeypatch.setattr(settings, "public_max_offset", 5)
    resp = client.get(
        "/api/v1/scientific/reactions/search?reactants=A&products=B&offset=6"
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "offset_too_large"
    assert body["context"] == {"offset_max": 5, "offset": 6}


def test_an_offset_on_the_boundary_still_succeeds(client, monkeypatch):
    """Exactly at the cap is accepted; the refusal is ``>``, not ``>=``."""
    monkeypatch.setattr(settings, "public_max_offset", 5)
    resp = client.get(
        "/api/v1/scientific/reactions/search?reactants=A&products=B&offset=5"
    )
    assert resp.status_code == 200, resp.text


def test_geometry_too_large_publishes_the_cap_and_the_atom_count(
    client, db_session, monkeypatch
):
    """Both numbers, and the second one is the arguable half.

    ``atoms`` is a server-side measurement, so it looks like the thing
    the disclosure rule withholds. It is not: it is the atom count of one
    record the caller named by handle -- chemistry, not a count of
    TCKDB's holdings -- and it is strictly less than this same endpoint
    returns for any request under the cap, which lists every atom
    individually. Nothing about the size of the corpus is recoverable
    from it.
    """
    from tests.services.scientific_read._factories import make_geometry

    geom = make_geometry(db_session, natoms=12, xyz_text="placeholder")
    monkeypatch.setattr(settings, "max_geometry_atoms_public", 5)

    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "geometry_too_large"
    assert body["context"] == {"max_atoms": 5, "atoms": 12}


def test_smiles_too_long_publishes_the_maximum_and_the_supplied_length(
    client,
):
    """A schema-layer cap, raised from the wire package's error type.

    ``app.schemas.reads`` sits on the wire side and raises
    ``CodedValidationError`` rather than the backend's subclass; the same
    handler reads both, which is what this asserts through the wire.

    ``context`` carries the length and not the string. That is a choice
    about ``context`` only, and the assertion below says so precisely
    rather than over-claiming: on this path ``detail`` is FastAPI's
    request-validation error, which echoes the offending ``input`` back
    verbatim -- documented behaviour that ``app.api.error_contract``
    already names as the reason clients must not parse ``detail``.
    Nothing about that is a disclosure problem: the string is the
    caller's own, which the rule on ``Shape`` permits explicitly. It is
    a reason not to duplicate a 2 KB value into ``context`` as well.
    """
    too_long = "C" * (MAX_SMILES_LENGTH + 1)
    resp = client.post(
        "/api/v1/scientific/kinetics/search",
        json={"reactants": [too_long], "products": ["O"]},
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "smiles_too_long"
    assert body["context"] == {
        "max_length": MAX_SMILES_LENGTH,
        "length": MAX_SMILES_LENGTH + 1,
    }
    assert too_long not in json.dumps(body["context"]), (
        "context duplicated the caller's SMILES; the length is the whole "
        "repair and the string only grows the body"
    )


def test_a_smiles_on_the_length_boundary_is_accepted(client):
    """Exactly at the maximum passes the bound.

    A 2048-character carbon chain matches nothing, so this asserts the
    *bound* rather than the search: a 200 with no records is the right
    answer and a 422 is not.
    """
    at_limit = "C" * MAX_SMILES_LENGTH
    resp = client.post(
        "/api/v1/scientific/kinetics/search",
        json={"reactants": [at_limit], "products": ["O"]},
    )
    assert resp.status_code == 200, resp.text


def test_query_too_expensive_publishes_the_cap_but_not_the_row_count(
    client, db_session, monkeypatch
):
    """The disclosure line, asserted as an absence.

    ``context`` carries ``section`` -- which sub-array was the offender,
    and therefore what to drop from ``include=`` -- and that section's
    ``cap``. It does **not** carry ``len(block)``, which is how many
    calculation rows TCKDB holds for this record.

    Why that matters: with the count published, a caller sets the cap
    low, sweeps every record, and reads TCKDB's holdings profile and
    roughly its upload schedule off a stream of cheap 422s. That is the
    enumeration exposure ``docs/specs/public_identifier_policy.md``
    (§"Why this matters now", item 3) refuses to leak through primary
    keys, and it would be worse here: a key leaks the count once, a
    countable refusal leaks it per query, forever.

    The absence is asserted over the **whole serialized body**, not just
    ``context``. ``detail`` is published too, and it carried the count
    until this change -- an omission from one and not the other would be
    decorative.
    """
    entry = _seed_reaction_entry_with_n_calculations(db_session, label="Q", n=7)
    monkeypatch.setattr(settings, "max_full_calculations_public", 1)

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=calculations"
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "query_too_expensive"
    assert body["context"] == {"section": "calculations", "cap": 1}

    # 7 is the measured value, and 1 is the cap, so the two cannot be
    # confused. Integers are extracted as literals rather than
    # substring-matched, so a leaked ``17`` somewhere would not be read
    # as a leaked ``7`` and a public ref containing the digit would not
    # fail this.
    serialized = json.dumps(body)
    assert 7 not in {int(t) for t in re.findall(r"\d+", serialized)}, (
        "the refusal published how many calculation rows TCKDB holds for "
        "this record. That is a measurement of the corpus, not of the "
        "request: see the disclosure line on app.api.code_catalogue.Shape. "
        f"Body: {serialized}"
    )


def test_a_full_expansion_inside_the_cap_still_returns_its_rows(
    client, db_session, monkeypatch
):
    """The accept-half for the same section, seeded identically.

    Without it, every assertion above is satisfied by a ``/full`` route
    that refuses ``include=calculations`` unconditionally -- and the
    absence assertion in particular is satisfied by a route that returns
    no numbers at all.
    """
    entry = _seed_reaction_entry_with_n_calculations(db_session, label="P", n=7)
    monkeypatch.setattr(settings, "max_full_calculations_public", 10)

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=calculations"
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["calculations"]) == 7
