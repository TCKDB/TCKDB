"""Nine catalogued refusals a depositor can provoke, none of which any test produced.

Why this file exists
--------------------
``app.api.code_catalogue`` is a promise: every code it lists is a code the
API can put in the ``code`` field of an error body, at the status it
declares. ``backend/docs/reviews/error_code_coverage_triage.md`` measured
that promise against the suite and found 53 codes no test has ever made a
client receive. A code nobody provokes is a claim nobody has checked —
the catalogue, the client's ``RejectionCode`` enum, and the runtime
observer all agree with each other and none of them has seen the wire.

This file closes the triage's Tier D ("upload payloads", eight codes) and
Tier E ("stored-state corruption", one). Each test issues a real request
through the HTTP route and asserts the ``(status, code)`` pair a client
actually receives. None of them asserts on a substring of ``detail``:
FastAPI echoes the rejected body back inside its Pydantic error string,
so a substring check passes even when the field it names was *accepted*.

The half that does the real work
--------------------------------
Every refusal here is paired with a test that sends the same payload
**without** the target fault and asserts it is accepted. Tier D is where
that matters most. Building an upload payload that is wrong in exactly
one way is the whole difficulty: a bundle with two faults is refused for
whichever validator runs first, and a test written against the wrong one
passes forever while proving nothing about the code in its name. PR #181
sharpened this — every geometry attached to a calculation must now be
made of the atoms of the subject it is filed under — so a payload
assembled carelessly is refused for composition and never reaches the
rule under test. The accept-half is what detects that, and each fixture
docstring states what makes the payload valid apart from the one fault.

Two corrections to the triage, recorded where the test is
---------------------------------------------------------
* Tier E's row says to provoke ``artifact_integrity_failed`` by
  corrupting a stored object and **downloading** it. When this file was
  written that was measurably false — the download route caught
  ``ArtifactIntegrityError`` itself and re-raised a bare
  ``HTTPException(502)``, so the app-level handler that mints the code
  never ran and a client received ``http_502``. #212 repaired it: the
  route still records the custody break ADR 0014 requires, then re-raises
  the exception unchanged. The triage row is now right, and both paths
  are asserted here — the upload one at
  ``test_uploading_over_a_corrupt_content_addressed_object_is_refused``,
  the download one at
  ``test_the_download_route_publishes_the_same_code_for_the_same_break``.
  The sweep that came with #212 found the sibling ``except
  ArtifactStorageUnavailable`` in the same function doing the same thing
  at 503, which no task had noticed; it is asserted here too.
* Tier D calls ``composed_search_candidate_limit_exceeded`` and
  ``composed_search_pagination_changed`` "upload payloads". They are
  composed *reads*; the tier heading is loose, the anchors are right.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select

from app.api.config import settings
from app.db.models.calculation import ArtifactIntegrityEvent
from app.services.scientific_read import thermo_search as thermo_search_mod
from app.services.scientific_read.export import iter_export_ndjson
from app.services.scientific_read.ml_dataset import iter_ml_species_ndjson
from tests.api.test_api_scientific_rejection_codes import (
    _XYZ_CH3,
    _XYZ_CH4,
    _XYZ_H,
    _bundle,
    _bundle_species,
    _map,
)
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
)
from tests.services.test_artifact_integrity import _SessionProxy

_REACTION_URL = "/api/v1/uploads/computed-reaction"
_KINETICS_URL = "/api/v1/uploads/kinetics"
_THERMO_SEARCH_URL = "/api/v1/scientific/thermo/search"
_EXPORT_URL = "/api/v1/scientific/export/ndjson"
_ML_EXPORT_URL = "/api/v1/scientific/export/ml/species.ndjson"


def _assert_code(response, expected: str, *, status: int = 422) -> dict:
    """The response carries *expected* in its ``code`` field, at *status*.

    Both halves are asserted because the catalogue declares both, and the
    runtime observer compares the pair. A right code at a wrong status is
    a client reading retry advice off a number that is not true.
    """
    assert response.status_code == status, response.text
    body = response.json()
    assert body.get("code") == expected, (
        f"expected code={expected!r} at {status}, got {body.get('code')!r} "
        f"at {response.status_code}. detail={body.get('detail')!r}"
    )
    return body


# ===========================================================================
# Atom mapping across a reaction (three codes, one bundle)
# ===========================================================================
#
# ``_bundle()`` is ``CH3 + H -> CH4`` with its saddle point and ``_map()``
# is the one correct map for it, both imported from
# ``tests/api/test_api_scientific_rejection_codes.py`` rather than copied:
# that bundle is already asserted to upload cleanly there, so anything
# these tests break is the only thing broken.


def test_the_unmodified_bundle_and_its_correct_map_are_accepted(client) -> None:
    """The accept-half every atom-map test below leans on.

    Without it, all three refusals would still pass if the shared fixture
    had drifted into being invalid for an unrelated reason — a wrong
    charge, a geometry that no longer matches its SMILES under #181's
    composition rule — and every computed-reaction upload were answering
    422 regardless of the map.
    """
    assert client.post(_REACTION_URL, json=_bundle(_map())).status_code == 201


def test_an_atom_map_on_a_reaction_with_no_transition_state_is_refused(
    client,
) -> None:
    """A map with nothing to point at.

    Both legs of an atom map run toward the saddle point (ADR 0011), so a
    barrierless channel cannot carry one. The bundle is otherwise the
    accepted one and the map is the *correct* map — only the transition
    state is removed — which is what makes this the map's refusal and not
    a complaint about the map's contents.
    """
    payload = _bundle(_map())
    del payload["transition_state"]
    body = _assert_code(
        client.post(_REACTION_URL, json=payload),
        "atom_map_without_transition_state",
    )
    # The structured half names the field, and carries no row id
    # (DR-0028 Req. 2).
    assert body["context"] == {"field_path": "atom_map"}


def test_the_same_reaction_without_a_map_is_accepted_without_a_transition_state(
    client,
) -> None:
    """The accept-half: a barrierless deposit is legitimate.

    This is the pair to the test above and the reason its refusal is
    about the *map*. A reaction with no saddle point and no map is a
    true, incomplete record; TCKDB accepts it and warns. If this returned
    422 the test above would be passing on "reactions without a
    transition state are refused", which is a different and wrong claim.
    """
    payload = _bundle()
    del payload["transition_state"]
    assert client.post(_REACTION_URL, json=payload).status_code == 201


def test_a_map_naming_a_participant_slot_the_reaction_does_not_declare(
    client,
) -> None:
    """``product 2`` on a reaction with one product.

    Every other participant mapping is the correct one, so the only thing
    the reaction and the map disagree about is whether a second product
    exists.
    """
    atom_map = _map()
    atom_map["participants"].append(
        {
            "side": "product",
            "species_key": "ch4",
            "participant_index": 2,
            "geometry_key": "ch4-geom",
            "atom_to_ts": {1: 1},
        }
    )
    body = _assert_code(
        client.post(_REACTION_URL, json=_bundle(atom_map)),
        "atom_map_participant_not_declared",
    )
    assert body["context"] == {"side": "product", "participant_index": 2}


def test_a_map_naming_the_wrong_species_in_a_declared_slot(client) -> None:
    """The slot exists; the map says a different species is in it.

    ``reactant 1`` is methyl and the map calls it the hydrogen atom. The
    indices it carries are methyl's correct four, so an index-range or
    element rule cannot be what fires — only the identity of the
    participant is wrong. This is a second raise site under the same
    code, and a client branching on the code must reach it too.
    """
    atom_map = _map()
    atom_map["participants"][0]["species_key"] = "h"
    body = _assert_code(
        client.post(_REACTION_URL, json=_bundle(atom_map)),
        "atom_map_participant_not_declared",
    )
    assert body["context"] == {
        "side": "reactant",
        "participant_index": 1,
        "declared_species_key": "ch3",
        "mapped_species_key": "h",
    }


def test_a_map_naming_one_participant_twice(client) -> None:
    """Methane mapped twice, identically.

    The duplicate is a byte-for-byte copy of a mapping the reaction does
    accept, so nothing about its contents is refusable; a participant
    molecule is mapped at most once, and this is the third raise site
    under the code. It fires on the nested ``ReactionAtomMapIn``
    validator rather than on the request-level one, which is why it is
    worth a separate test: a different validator, the same published
    contract.
    """
    atom_map = _map()
    atom_map["participants"].append(copy.deepcopy(atom_map["participants"][2]))
    body = _assert_code(
        client.post(_REACTION_URL, json=_bundle(atom_map)),
        "atom_map_participant_not_declared",
    )
    assert body["context"] == {"side": "product", "participant_index": 1}


def test_a_participant_geometry_the_map_cannot_count(client) -> None:
    """Methyl's XYZ header says five atoms and four lines follow.

    A map is checked by counting indices into a geometry, so a geometry
    that cannot be counted cannot have a map checked against it. The map
    itself is the correct one and every other geometry is untouched;
    only methyl's header digit changes, and the atoms it lists are still
    methyl's, so #181's composition rule has nothing to object to.
    """
    broken_ch3 = "5\n" + _XYZ_CH3.split("\n", 1)[1]
    species = [
        _bundle_species("ch3", "[CH3]", 2, broken_ch3),
        _bundle_species("h", "[H]", 2, _XYZ_H),
        _bundle_species("ch4", "C", 1, _XYZ_CH4),
    ]
    body = _assert_code(
        client.post(_REACTION_URL, json=_bundle(_map(), species=species)),
        "atom_map_geometry_unparseable",
    )
    assert body["context"] == {"declared_atoms": 5, "coordinate_lines": 4}


def test_a_saddle_point_geometry_the_map_cannot_count(client) -> None:
    """The same refusal on the transition state's own geometry.

    The saddle point is parsed before any participant is, so this is a
    distinct raise path reaching the same code, and it is the one that
    fires for a map that is otherwise complete and correct.
    """
    payload = _bundle(_map())
    ts_geometry = payload["transition_state"]["geometry"]
    ts_geometry["xyz_text"] = "6\n" + ts_geometry["xyz_text"].split("\n", 1)[1]
    body = _assert_code(
        client.post(_REACTION_URL, json=payload),
        "atom_map_geometry_unparseable",
    )
    assert body["context"] == {"declared_atoms": 6, "coordinate_lines": 5}


# ===========================================================================
# A rate coefficient whose reaction order has no unit system
# ===========================================================================

_H_ATOM = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
_H2 = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}


def _kinetics_payload(reactants: list[dict], products: list[dict], **overrides) -> dict:
    """A minimal experimental Arrhenius deposit for the given participants."""
    payload: dict = {
        "reaction": {
            "reversible": True,
            "reactants": [{"species_entry": e} for e in reactants],
            "products": [{"species_entry": e} for e in products],
        },
        "scientific_origin": "experimental",
        "a": 1.0e10,
        "n": 0.0,
        "reported_ea": 1.0,
        "reported_ea_units": "kj_mol",
    }
    payload.update(overrides)
    return payload


def test_a_rate_coefficient_for_a_four_body_reaction_has_no_a_units(client) -> None:
    """``4 H -> 2 H2`` with an ``a_units``: no unit system exists to check it against.

    ``reactants`` is bounded below at one and not above
    (``schemas/workflows/kinetics_upload.py``), so molecularity 4 is
    expressible; ``_A_UNITS_BY_ORDER`` stops at 3. The reaction is
    deliberately balanced in mass and charge, so the conservation rules
    that guard this endpoint have nothing to say, and the A-factor and
    activation energy are ordinary numbers. The only thing wrong is that
    a fourth-order rate constant has no declared dimensionality.
    """
    body = _assert_code(
        client.post(
            _KINETICS_URL,
            json=_kinetics_payload(
                [_H_ATOM] * 4, [_H2, _H2], a_units="cm3_mol_s"
            ),
        ),
        "unsupported_reaction_molecularity",
    )
    assert body["context"] == {"molecularity": 4}


def test_the_same_four_body_reaction_without_a_units_is_accepted(client) -> None:
    """The accept-half, and it is the interesting one.

    ``a_units`` is optional. Dropping it accepts the identical
    four-reactant reaction, which proves the refusal above is about the
    unit system and not about TCKDB refusing four-body chemistry — and
    proves the payload was valid in every other respect, mass balance
    included.
    """
    response = client.post(
        _KINETICS_URL, json=_kinetics_payload([_H_ATOM] * 4, [_H2, _H2])
    )
    assert response.status_code == 201, response.text


def test_a_third_body_raises_the_order_into_the_same_refusal(client) -> None:
    """Three reactants plus a generic ``+M`` collider is order four.

    The second way to reach the code, and the one a depositor is far more
    likely to write by accident: the reaction looks termolecular and the
    units are correct termolecular units, but a simple third body puts a
    ``[M]`` on the main line and the effective order becomes four.
    """
    body = _assert_code(
        client.post(
            _KINETICS_URL,
            json=_kinetics_payload(
                [_H_ATOM] * 3,
                [_H2, _H_ATOM],
                a_units="cm6_mol2_s",
                is_third_body=True,
            ),
        ),
        "unsupported_reaction_molecularity",
    )
    assert body["context"] == {"molecularity": 4}


def test_the_same_deposit_without_the_third_body_is_accepted(client) -> None:
    """The accept-half for the third-body route: order three is fine.

    Same reaction, same termolecular units, ``is_third_body`` dropped.
    Its acceptance is what makes the refusal above attributable to the
    collider rather than to the units or the participants.
    """
    response = client.post(
        _KINETICS_URL,
        json=_kinetics_payload(
            [_H_ATOM] * 3, [_H2, _H_ATOM], a_units="cm6_mol2_s"
        ),
    )
    assert response.status_code == 201, response.text


# ===========================================================================
# Composed searches that cannot finish the traversal they started
# ===========================================================================
#
# A composed endpoint refuses to pretend the first page is the whole
# answer. Both codes below are properties of the *traversal*, not of the
# request, so both need a corpus and one of them needs a writer.
#
# Three pentane isomers share the formula C5H12 and are three distinct
# species under TCKDB's ``(smiles, charge, multiplicity)`` identity, which
# is exactly what a formula search has to enumerate.

_PENTANE = "CCCCC"
_ISOPENTANE = "CC(C)CC"
_NEOPENTANE = "CC(C)(C)C"


def _seed_species(session, smiles_list) -> None:
    for smiles in smiles_list:
        species = make_species(session, smiles=smiles, charge=0, multiplicity=1)
        make_species_entry(session, species)
    session.flush()


@pytest.fixture
def two_pentanes(db_session):
    """Two C5H12 species, so a formula search has two candidates to page over."""
    _seed_species(db_session, [_PENTANE, _ISOPENTANE])
    return db_session


def test_a_composed_search_refuses_a_candidate_set_it_cannot_traverse(
    client, two_pentanes, monkeypatch
) -> None:
    """More candidates than the hosted offset bound can reach.

    The bound is ``public_max_offset + page_size``. Lowering both to make
    it 1 is how this is reached without seeding ten thousand species;
    they are the deployment's own abuse-control knobs and nothing else
    about the request changes. The request itself is a perfectly ordinary
    formula search whose ``limit`` is inside the lowered cap, so the
    pagination validator has nothing to refuse — the refusal comes from
    the composed traversal discovering it cannot see the whole candidate
    set, which is the one thing it must never paper over.
    """
    monkeypatch.setattr(settings, "public_max_limit", 1)
    monkeypatch.setattr(settings, "public_max_offset", 0)
    _assert_code(
        client.post(_THERMO_SEARCH_URL, json={"formula": "C5H12", "limit": 1}),
        "composed_search_candidate_limit_exceeded",
    )


def test_the_same_search_within_the_bound_is_accepted(
    client, two_pentanes
) -> None:
    """The accept-half: two candidates under the shipped bound is fine.

    Byte-identical request, default settings. Its 200 is what makes the
    refusal above attributable to the bound rather than to the formula,
    the limit, or the seeded corpus.
    """
    response = client.post(
        _THERMO_SEARCH_URL, json={"formula": "C5H12", "limit": 1}
    )
    assert response.status_code == 200, response.text


def test_a_composed_search_refuses_a_candidate_set_that_changed_under_it(
    client, two_pentanes, monkeypatch
) -> None:
    """A third species is deposited between page one and page two.

    The read session sets no isolation level, so it runs READ COMMITTED
    and a concurrent depositor's commit becomes visible mid-traversal —
    genuinely reachable in production and not reachable from any single
    payload, so the row has to be injected. It is injected by letting the
    real page fetch run and then writing an ordinary third pentane, which
    is what the concurrent depositor would have done; nothing about the
    count the traversal compares is faked. ``public_max_limit`` is
    lowered only to make the traversal take more than one page.
    """
    monkeypatch.setattr(settings, "public_max_limit", 1)

    real_search_species = thermo_search_mod.search_species
    pages_fetched = {"n": 0}

    def inject_a_concurrent_deposit_after_the_first_page(session, request):
        result = real_search_species(session, request)
        pages_fetched["n"] += 1
        if pages_fetched["n"] == 1:
            _seed_species(session, [_NEOPENTANE])
        return result

    monkeypatch.setattr(
        thermo_search_mod,
        "search_species",
        inject_a_concurrent_deposit_after_the_first_page,
    )
    _assert_code(
        client.post(_THERMO_SEARCH_URL, json={"formula": "C5H12", "limit": 1}),
        "composed_search_pagination_changed",
    )
    assert pages_fetched["n"] == 2, (
        "the refusal must come from the second page disagreeing with the "
        f"first, not from the first page alone (pages={pages_fetched['n']})"
    )


def test_the_same_traversal_with_no_concurrent_deposit_is_accepted(
    client, two_pentanes, monkeypatch
) -> None:
    """The accept-half: the same multi-page traversal, nobody writing.

    The wrapper is still installed and still counts pages; it just writes
    nothing. Two pages are fetched and the search returns 200, so the
    refusal above is the deposit and not the paging.
    """
    monkeypatch.setattr(settings, "public_max_limit", 1)

    real_search_species = thermo_search_mod.search_species
    pages_fetched = {"n": 0}

    def count_only(session, request):
        result = real_search_species(session, request)
        pages_fetched["n"] += 1
        return result

    monkeypatch.setattr(thermo_search_mod, "search_species", count_only)
    response = client.post(
        _THERMO_SEARCH_URL, json={"formula": "C5H12", "limit": 1}
    )
    assert response.status_code == 200, response.text
    assert pages_fetched["n"] >= 2, pages_fetched


# ===========================================================================
# Bulk exports that refuse to start
# ===========================================================================
#
# ``all=true`` is the one export request whose size is not stated by the
# caller, so both export families cap it. The cap is a def-time default
# argument (``DEFAULT_ALL_CAP = 50_000``) which neither route exposes and
# no setting overrides, so the only honest way to cross it in a test is to
# lower it — the triage's own suggestion ("cheapest by passing a small
# all_cap"). That it cannot be lowered by configuration is worth its own
# task; it is not a reason to leave the refusal unmeasured.


def test_an_all_reactions_export_over_its_cap_is_refused(
    client, login_as, _api_curator_user, monkeypatch
) -> None:
    """One reaction in the corpus, a cap of zero.

    The seed is a real reaction deposited through the ordinary upload
    route, so the count in the refusal is a count of real rows. The
    request is the same one the accept-half makes.
    """
    assert client.post(_REACTION_URL, json=_bundle(_map())).status_code == 201
    login_as(_api_curator_user)
    monkeypatch.setitem(iter_export_ndjson.__kwdefaults__, "all_cap", 0)
    _assert_code(
        client.get(
            _EXPORT_URL, params={"all": "true", "min_review_status": "under_review"}
        ),
        "export_all_cap_exceeded",
    )


def test_the_same_export_under_its_cap_is_accepted(
    client, login_as, _api_curator_user
) -> None:
    """The accept-half: the shipped cap streams the same corpus.

    Same seed, same request, cap untouched. Without it the test above
    would pass equally well against a curator gate that refused
    everything, or an export that had stopped working.
    """
    assert client.post(_REACTION_URL, json=_bundle(_map())).status_code == 201
    login_as(_api_curator_user)
    response = client.get(
        _EXPORT_URL, params={"all": "true", "min_review_status": "under_review"}
    )
    assert response.status_code == 200, response.text


_CONFORMER_PAYLOAD = {
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
    "calculation": {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
    },
}


def test_an_all_species_ml_export_over_its_cap_is_refused(
    client, login_as, _api_curator_user, monkeypatch
) -> None:
    """The ML dataset's own cap, which is a separate code from the native one.

    Two exports, two caps, two codes: a client that branches on
    ``export_all_cap_exceeded`` learns nothing about the ML surface, and
    the catalogue says so. One species entry is deposited so the refusal
    counts something real.
    """
    assert (
        client.post("/api/v1/uploads/conformers", json=_CONFORMER_PAYLOAD).status_code
        == 201
    )
    login_as(_api_curator_user)
    monkeypatch.setitem(iter_ml_species_ndjson.__kwdefaults__, "all_cap", 0)
    _assert_code(
        client.get(
            _ML_EXPORT_URL,
            params={"all": "true", "min_review_status": "under_review"},
        ),
        "ml_export_all_cap_exceeded",
    )


def test_the_same_ml_export_under_its_cap_is_accepted(
    client, login_as, _api_curator_user
) -> None:
    """The accept-half for the ML surface."""
    assert (
        client.post("/api/v1/uploads/conformers", json=_CONFORMER_PAYLOAD).status_code
        == 201
    )
    login_as(_api_curator_user)
    response = client.get(
        _ML_EXPORT_URL, params={"all": "true", "min_review_status": "under_review"}
    )
    assert response.status_code == 200, response.text


# ===========================================================================
# Tier E — the object store holds bytes that are not what its key says
# ===========================================================================


class _InMemoryObjectStore:
    """A stand-in for S3/MinIO whose contents the test controls.

    Only the transport is faked. ``store_artifact`` and
    ``load_artifact_bytes`` run for real against it, so the digest
    comparison that detects the break is the production one and the
    exception is the production exception. Corrupting an entry here is
    the closest a test can get to the event the guard exists for — bit
    rot, an operator overwrite, a store that returns the wrong object —
    and it fabricates nothing in the database.
    """

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})

    @staticmethod
    def _not_found(operation: str) -> ClientError:
        return ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, operation)

    def head_bucket(self, Bucket):
        return {}

    def create_bucket(self, Bucket):
        return {}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self._not_found("HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self._not_found("GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body
        return {}


_ARTIFACT_BYTES = b"the bytes this depositor is uploading\n"
_ARTIFACT_SHA256 = hashlib.sha256(_ARTIFACT_BYTES).hexdigest()
_ARTIFACT_KEY = f"{_ARTIFACT_SHA256[:2]}/{_ARTIFACT_SHA256}"


def _artifact_request() -> dict:
    return {
        "artifacts": [
            {
                "kind": "ancillary",
                "filename": "note.txt",
                "content_base64": base64.b64encode(_ARTIFACT_BYTES).decode("ascii"),
            }
        ]
    }


def _a_calculation(client) -> int:
    response = client.post("/api/v1/uploads/conformers", json=_CONFORMER_PAYLOAD)
    assert response.status_code == 201, response.text
    return response.json()["primary_calculation"]["calculation_id"]


def _use_object_store(monkeypatch, db_session, store) -> None:
    monkeypatch.setattr(
        "app.services.artifact_storage._get_s3_client", lambda: store
    )
    # The custody recorder writes in its own transaction on purpose; that
    # durability is asserted against a real engine in
    # ``tests/services/test_artifact_integrity``. Here the subject is what
    # the *route* publishes, so the boundary is neutered and the per-test
    # rollback keeps owning cleanup.
    monkeypatch.setattr("app.api.deps.SessionLocal", _SessionProxy(db_session))


def test_uploading_over_a_corrupt_content_addressed_object_is_refused(
    client, db_session, monkeypatch
) -> None:
    """A depositor's upload lands on a key whose stored bytes are already wrong.

    This is the request that publishes ``artifact_integrity_failed``, and
    it is not the one the triage names (see this module's docstring).
    ``store_artifact`` treats a HEAD hit as dedup only after re-reading
    and re-verifying the object, because attaching a second row to a
    shared key blesses bytes nobody checked. The upload itself is
    entirely valid — a real calculation, a real base64 body, a kind and
    filename the schema accepts — and the store is intact in every
    respect except that one object.

    502, not 503: a store that answers with the wrong bytes is not an
    outage, and retrying cannot clear it.
    """
    calculation_id = _a_calculation(client)
    _use_object_store(
        monkeypatch,
        db_session,
        _InMemoryObjectStore({_ARTIFACT_KEY: b"bytes that hash to something else"}),
    )

    body = _assert_code(
        client.post(
            f"/api/v1/calculations/{calculation_id}/artifacts",
            json=_artifact_request(),
        ),
        "artifact_integrity_failed",
        status=502,
    )
    # The public body says a subsystem failed and stops there: no digest,
    # no row id, nothing that would let a caller probe stored content
    # (DR-0028 Req. 2).
    assert body["context"] == {}

    # A log is not a record (ADR 0014). The break outlives the request
    # that discovered it, keyed by the digest rather than by any row.
    events = db_session.scalars(
        select(ArtifactIntegrityEvent).where(
            ArtifactIntegrityEvent.sha256 == _ARTIFACT_SHA256
        )
    ).all()
    assert len(events) == 1, events


def test_the_same_upload_into_an_intact_store_is_accepted(
    client, db_session, monkeypatch
) -> None:
    """The accept-half: nothing about the upload is wrong.

    Same calculation, same bytes, same request — an empty store instead
    of a corrupt one. Its 201 is what makes the 502 above a statement
    about TCKDB's custody of the object rather than about the payload.
    """
    calculation_id = _a_calculation(client)
    _use_object_store(monkeypatch, db_session, _InMemoryObjectStore())

    response = client.post(
        f"/api/v1/calculations/{calculation_id}/artifacts",
        json=_artifact_request(),
    )
    assert response.status_code == 201, response.text


def test_a_second_upload_of_the_same_bytes_verifies_the_stored_object(
    client, db_session, monkeypatch
) -> None:
    """Dedup is a re-verification, and the second upload is where it runs.

    The first upload writes the object; the second finds the key present
    and re-reads it. Corrupting the store between the two puts the break
    exactly where a content-addressed store can develop one — after a
    successful write — and shows the refusal is not an artefact of
    pre-seeding.
    """
    calculation_id = _a_calculation(client)
    store = _InMemoryObjectStore()
    _use_object_store(monkeypatch, db_session, store)

    first = client.post(
        f"/api/v1/calculations/{calculation_id}/artifacts", json=_artifact_request()
    )
    assert first.status_code == 201, first.text

    store.objects[_ARTIFACT_KEY] = b"rot"

    _assert_code(
        client.post(
            f"/api/v1/calculations/{calculation_id}/artifacts",
            json=_artifact_request(),
        ),
        "artifact_integrity_failed",
        status=502,
    )


def _an_approved_artifact(db_session):
    """One approved artifact whose row claims ``_ARTIFACT_SHA256``.

    The store is left to the caller, which is the whole variable: what
    the download route answers is decided by what the object store hands
    back for this key, and each test below hands back something else.
    """
    from app.db.models.common import RecordReviewStatus, SubmissionRecordType
    from tests.api.scientific.test_api_scientific_artifacts import (
        _make_species_owned_calc,
    )
    from tests.services.scientific_read._factories import attach_artifact, set_review

    _species, _entry, calculation = _make_species_owned_calc(db_session)
    artifact = attach_artifact(db_session, calculation=calculation)
    artifact.sha256 = _ARTIFACT_SHA256
    artifact.bytes = len(_ARTIFACT_BYTES)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calculation.id,
        status=RecordReviewStatus.approved,
    )
    db_session.flush()
    return artifact


def test_the_download_route_publishes_the_same_code_for_the_same_break(
    client, db_session, monkeypatch
) -> None:
    """One condition, one contract, whichever route noticed the break.

    This is the rewrite the previous version of this test asked for. It
    used to assert ``http_502`` — what the route really answered — and
    said in its own docstring that the repair was to let the typed
    exception reach the app-level handler, and that it should then be
    rewritten to assert ``artifact_integrity_failed`` rather than
    deleted. The route now records the custody break and re-raises the
    exception unchanged, so the handler in ``app.api.errors`` mints the
    code, exactly as it does on the upload path.

    The property being kept is the one the old docstring named: a client
    must be able to tell an integrity break from a generic gateway
    failure. Losing it still costs a red test — this one — because
    ``http_502`` is what any bare ``HTTPException(502)`` here would
    produce.

    The custody record is asserted below and not merely assumed. Letting
    the exception through is only correct if the row is still written
    first; a repair that traded ADR 0014's durable record for a nicer
    code would be a worse contract, not a better one.
    """
    artifact = _an_approved_artifact(db_session)

    _use_object_store(
        monkeypatch,
        db_session,
        _InMemoryObjectStore({_ARTIFACT_KEY: b"bytes that hash to something else"}),
    )
    body = _assert_code(
        client.get(f"/api/v1/scientific/artifacts/{_ARTIFACT_SHA256}/download"),
        "artifact_integrity_failed",
        status=502,
    )
    # DR-0028 Req. 2 — a subsystem is named and nothing else. No digest,
    # no artifact id, nothing a caller could use to probe stored content.
    assert body["context"] == {}, body
    assert str(artifact.id) not in str(body.get("detail")), body

    # ADR 0014: the break outlives the request that discovered it. This is
    # the half that must survive the code change.
    events = db_session.scalars(
        select(ArtifactIntegrityEvent).where(
            ArtifactIntegrityEvent.sha256 == _ARTIFACT_SHA256
        )
    ).all()
    assert len(events) == 1, events
    assert events[0].detected_during.value == "download", events[0].detected_during


def test_an_intact_object_still_downloads(client, db_session, monkeypatch) -> None:
    """The accept-half: nothing about the request or the row is wrong.

    Without it, the 502 above is satisfied by a route that refuses every
    download, and the custody assertion by a route that records a break
    on every read.
    """
    _an_approved_artifact(db_session)
    _use_object_store(
        monkeypatch,
        db_session,
        _InMemoryObjectStore({_ARTIFACT_KEY: _ARTIFACT_BYTES}),
    )
    response = client.get(
        f"/api/v1/scientific/artifacts/{_ARTIFACT_SHA256}/download"
    )
    assert response.status_code == 200, response.text
    assert response.content == _ARTIFACT_BYTES

    events = db_session.scalars(
        select(ArtifactIntegrityEvent).where(
            ArtifactIntegrityEvent.sha256 == _ARTIFACT_SHA256
        )
    ).all()
    assert events == [], events


def test_a_missing_object_names_the_storage_subsystem_too(
    client, db_session, monkeypatch
) -> None:
    """The sibling ``except`` in the same function, found by the sweep.

    ``ArtifactStorageUnavailable`` had the identical defect and nobody
    had filed it: caught for its side effect — recording ``object_missing``,
    which is durable information about custody, not an outage — and then
    re-raised as a bare ``HTTPException(503)``, so the download answered
    ``http_503`` where the upload path answers
    ``artifact_storage_unavailable``.

    Two typed exceptions, one function, one defect: fixing only the one
    with a task number would have left the other exactly as it was.
    """
    artifact = _an_approved_artifact(db_session)

    # An empty store: the object store answers, and says the key is not
    # there. That is the ``missing=True`` branch, not an outage.
    _use_object_store(monkeypatch, db_session, _InMemoryObjectStore())
    body = _assert_code(
        client.get(f"/api/v1/scientific/artifacts/{_ARTIFACT_SHA256}/download"),
        "artifact_storage_unavailable",
        status=503,
    )
    assert body["context"] == {}, body
    assert str(artifact.id) not in str(body.get("detail")), body

    events = db_session.scalars(
        select(ArtifactIntegrityEvent).where(
            ArtifactIntegrityEvent.sha256 == _ARTIFACT_SHA256
        )
    ).all()
    assert len(events) == 1, events
    assert events[0].finding.value == "object_missing", events[0].finding
