"""Every ``existing_*_id`` that names no row, asserted on the wire.

What changed, and what deliberately did not
--------------------------------------------
``/uploads/thermo`` and ``/uploads/statmech`` let a client cite a record
this API already stored, by quoting back the row id it was issued —
``existing_statmech_id``, ``source_calculations[i].existing_calculation_id``.
When the id named nothing they answered **404**, which is correct and is
unchanged here: nothing the depositor can retype makes a missing statmech
exist, so the honest answer is "the thing you named is not there" and the
repair is to deposit it first (see
:mod:`app.services.upload_reference` for why not 422).

What they did *not* do was say anything else. The body carried the generic
``resource_not_found`` and an **empty** ``context``, so a depositor whose
payload cited a statmech and three calculations learned only that one of
the four was missing. Since #230 each refusal carries the same code its
public-ref counterpart on ``/uploads/kinetics`` carries, plus a ``context``
naming the field and the kind of row.

Why the same code and not a new one
------------------------------------
#195 landed because the *status* used to depend on how a caller spelled
the name — 404 for a row id, 422 for a public ref, for one condition — and
"which spelling did you use" is not a distinction a depositor can act on.
Minting ``unknown_statmech_id`` alongside ``unknown_statmech_ref`` would
rebuild that defect one field down: the missing row is the same kind of
row and the repair is the same deposit through the same endpoint.
``context['field']`` is what separates the two, and it is the half a
client branches on. The cross-root agreement is pinned from the kinetics
side in ``test_api_kinetics_unknown_reference_codes.py`` and from this side
below, so neither can drift without the other failing.

The one thing the two spellings do not share
---------------------------------------------
Disclosure. A public ref is echoed into ``context['ref']``; a row id is
**not** echoed anywhere (DR-0028 Requirement 2 — a row id is an
implementation detail of one database instance, no public surface is keyed
on it, and a client that learns to read one builds against something TCKDB
never promised to keep stable). It goes to the log instead.
:func:`test_no_refusal_here_echoes_the_row_id` is the assertion, and it
checks the *whole serialised body* rather than ``context`` alone, because
the id would most plausibly leak back through the prose.

The shape of every test here
----------------------------
* the **exact status and code**, plus the structured ``context`` — never a
  substring of ``detail``;
* a **neighbouring request that must still be accepted**, because a test
  asserting only "a 404 arrived" passes against a handler that 404s on
  everything;
* no database primary key anywhere in the body.
"""

from __future__ import annotations

import pytest

from app.db.models.common import CalculationType
from app.db.models.statmech import Statmech
from app.services.upload_reference import (
    W_UNKNOWN_CALCULATION_REF,
    unknown_reference,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_THERMO = "/api/v1/uploads/thermo"
_STATMECH = "/api/v1/uploads/statmech"

#: Comfortably past any row this suite creates, and never a real id.
_ABSENT = 2_000_000_000


@pytest.fixture
def methyl(db_session):
    """One species entry, and an ``opt`` calculation that belongs to it.

    The calculation is real and correctly owned so that the *accepted*
    half of each test is refused by nothing: an id that names a row owned
    by another species entry is a different (422) refusal, and one that
    names a job of the wrong type is a third, either of which would fire
    first and make a green assertion here mean nothing.
    """
    entry = make_species_entry(
        db_session,
        make_species(
            db_session, smiles="[CH3]", multiplicity=2,
            inchi_key=next_inchi_key("EXID"),
        ),
    )
    calculation = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    statmech = Statmech(species_entry_id=entry.id, scientific_origin="computed")
    db_session.add(statmech)
    db_session.flush()
    return entry, calculation, statmech


_SPECIES = {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}


def _thermo(**overrides) -> dict:
    body: dict = {
        "species_entry": _SPECIES,
        "scientific_origin": "computed",
        "h298_kj_mol": 146.7,
    }
    body.update(overrides)
    return body


def _statmech(**overrides) -> dict:
    body: dict = {
        "species_entry": _SPECIES,
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
    }
    body.update(overrides)
    return body


def _missing(response, *, code: str, field: str, kind: str) -> dict:
    """Assert the 404 a client branches on, and return the body.

    ``detail`` is deliberately not matched: asserting the code and the
    structured ``context`` is what a client can act on, and a substring of
    the prose is not a contract.
    """
    assert response.status_code == 404, response.text[:800]
    body = response.json()
    assert body["code"] == code, body
    context = body["context"]
    assert context["field"] == field, body
    assert context["kind"] == kind, body
    # No row id, under any key: not the one the caller sent, not any other.
    leaked = [key for key in context if key == "id" or key.endswith("_id")]
    assert not leaked, f"{code} leaked database ids into context: {leaked}"
    assert str(_ABSENT) not in str(body), body
    return body


# ---------------------------------------------------------------------------
# /uploads/thermo -- existing_statmech_id
# ---------------------------------------------------------------------------


def test_a_thermo_citing_a_statmech_that_is_not_there_is_a_coded_404(
    client, methyl
):
    """An enthalpy cannot be anchored to a partition function that is absent.

    The repair is to deposit the statmech, which is a different request to
    a different endpoint — so the depositor is told the row is missing and
    which field named it, not that their payload is malformed.
    """
    _entry, _calc, statmech = methyl

    refused = client.post(_THERMO, json=_thermo(existing_statmech_id=_ABSENT))
    _missing(
        refused,
        code="unknown_statmech_ref",
        field="existing_statmech_id",
        kind="statmech",
    )

    accepted = client.post(
        _THERMO, json=_thermo(existing_statmech_id=statmech.id)
    )
    assert accepted.status_code == 201, accepted.text[:800]


# ---------------------------------------------------------------------------
# /uploads/thermo -- source_calculations[i].existing_calculation_id
# ---------------------------------------------------------------------------


def test_a_thermo_source_link_naming_no_calculation_is_a_coded_404(
    client, methyl
):
    """``context['field']`` carries the index, because a payload cites many.

    A thermo record may link several supporting jobs. "calculation not
    found" would not say which of them, which is the whole reason the
    index is in the field path rather than only in the prose.
    """
    _entry, calculation, _row = methyl

    refused = client.post(
        _THERMO,
        json=_thermo(
            source_calculations=[{"existing_calculation_id": _ABSENT, "role": "sp"}]
        ),
    )
    _missing(
        refused,
        code="unknown_calculation_ref",
        field="source_calculations[0].existing_calculation_id",
        kind="calculation",
    )

    accepted = client.post(
        _THERMO,
        json=_thermo(
            source_calculations=[
                {"existing_calculation_id": calculation.id, "role": "sp"}
            ]
        ),
    )
    assert accepted.status_code == 201, accepted.text[:800]


def test_the_second_thermo_source_link_reports_its_own_index(client, methyl):
    """The index is read off the loop, not hard-coded to zero.

    Without this the field path would be right by accident for every
    payload that cites exactly one calculation, which is most of them.
    """
    _entry, calculation, _row = methyl

    refused = client.post(
        _THERMO,
        json=_thermo(
            source_calculations=[
                {"existing_calculation_id": calculation.id, "role": "sp"},
                {"existing_calculation_id": _ABSENT, "role": "freq"},
            ]
        ),
    )
    _missing(
        refused,
        code="unknown_calculation_ref",
        field="source_calculations[1].existing_calculation_id",
        kind="calculation",
    )


# ---------------------------------------------------------------------------
# /uploads/statmech -- source_calculations[i].existing_calculation_id
# ---------------------------------------------------------------------------


def test_a_statmech_source_link_naming_no_calculation_is_a_coded_404(
    client, methyl
):
    """The sibling root, whose field path is namespaced ``statmech.``.

    Same code as thermo's and as the kinetics public-ref spelling: one
    kind of missing row, one repair. The field path is what differs, and
    it is the field path a client reads.
    """
    _entry, calculation, _row = methyl

    refused = client.post(
        _STATMECH,
        json=_statmech(
            source_calculations=[{"existing_calculation_id": _ABSENT, "role": "sp"}]
        ),
    )
    _missing(
        refused,
        code="unknown_calculation_ref",
        field="statmech.source_calculations[0].existing_calculation_id",
        kind="calculation",
    )

    accepted = client.post(
        _STATMECH,
        json=_statmech(
            source_calculations=[
                {"existing_calculation_id": calculation.id, "role": "sp"}
            ]
        ),
    )
    assert accepted.status_code == 201, accepted.text[:800]


# ---------------------------------------------------------------------------
# The disclosure rule, checked across every root at once
# ---------------------------------------------------------------------------


def test_no_refusal_here_echoes_the_row_id(client, methyl):
    """The id the caller sent appears in no part of the body.

    Checked over the whole serialised response rather than over
    ``context``, because the likeliest way for it to come back is inside
    the sentence — which is exactly how the thirty ``f"... ({kind}_id=
    {row_id})"`` 404s :func:`app.api.errors.not_found` replaced were
    written.
    """
    bodies = [
        client.post(_THERMO, json=_thermo(existing_statmech_id=_ABSENT)),
        client.post(
            _THERMO,
            json=_thermo(
                source_calculations=[
                    {"existing_calculation_id": _ABSENT, "role": "sp"}
                ]
            ),
        ),
        client.post(
            _STATMECH,
            json=_statmech(
                source_calculations=[
                    {"existing_calculation_id": _ABSENT, "role": "sp"}
                ]
            ),
        ),
    ]
    for response in bodies:
        assert response.status_code == 404, response.text[:400]
        assert str(_ABSENT) not in response.text, response.text[:400]


# ---------------------------------------------------------------------------
# The seam's own guard
# ---------------------------------------------------------------------------


def test_the_seam_refuses_to_guess_which_disclosure_rule_was_meant():
    """``ref=`` echoes and ``row_id=`` does not, so exactly one is required.

    A ``TypeError`` and not a coded 4xx: this is a caller of the seam
    getting it wrong, not a depositor. Defaulting either way is how a row
    id reaches the wire — passing an id as ``ref=`` would echo it, and
    silently dropping both would produce a refusal naming nothing.
    """
    with pytest.raises(TypeError):
        unknown_reference(
            code=W_UNKNOWN_CALCULATION_REF,
            field="f",
            kind="calculation",
            remedy="r",
        )
    with pytest.raises(TypeError):
        unknown_reference(
            code=W_UNKNOWN_CALCULATION_REF,
            field="f",
            kind="calculation",
            ref="calc_abc",
            row_id=7,
            remedy="r",
        )


def test_the_row_id_form_keeps_the_id_out_of_the_exception_it_builds():
    """The unit-level statement of the rule the wire tests check end to end."""
    error = unknown_reference(
        code=W_UNKNOWN_CALCULATION_REF,
        field="source_calculations[0].existing_calculation_id",
        kind="calculation",
        row_id=987_654_321,
        remedy="Deposit it first.",
    )
    assert error.code == W_UNKNOWN_CALCULATION_REF
    assert "987654321" not in str(error)
    assert "987654321" not in str(error.context)
    assert "ref" not in error.context
    assert error.context["kind"] == "calculation"
