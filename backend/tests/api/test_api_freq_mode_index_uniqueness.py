"""A frequency list that numbers two modes the same, on the wire.

What was wrong
--------------
``FreqResultPayload.validate_modes_consistency`` has always refused a
``modes`` list carrying the same ``mode_index`` twice, and refused it
with a bare ``ValueError``. Through a request that reaches the payload as
a nested model, FastAPI wraps it in a ``RequestValidationError`` and the
envelope reports ``code = "request_validation_error"`` — a code shared
with every other malformed field in the body, so a client could tell
"renumber your frequency list" from "your charge is a string" only by
matching English inside ``detail``.

Why this one is not a scientific check
--------------------------------------
Its immediate neighbour in the same validator,
``freq_n_imag_disagrees_with_modes``, *is* declared in
``app.scientific_checks``: a scalar and the evidence beside it answering
the same question about chemistry two different ways is a position a
referee could argue with, and the register's end-to-end proof of it lives
in ``test_api_scientific_rejection_codes.py``.

A repeated ``mode_index`` is not that. It is a malformed list — a
serialiser that concatenated two blocks, a producer that restarted its
counter — and asserts nothing about the potential energy surface. It
belongs in ``app.api.code_catalogue``, which enumerates what the API can
return and claims nothing about chemistry, and it reaches the client enum
from there. That split is what #161 built the catalogue for, and it is
why this file sits beside the scientific one rather than inside it.

What is asserted
----------------
``code`` and ``context``, never a substring of ``detail`` — a code
spelled inside a sentence is exactly the defect the catalogue's promotion
rule was narrowed to reject. Each refusal is paired with the payload that
must still be **accepted**, because a 422 assertion is satisfied just as
well by a guard that refuses every frequency list it is shown.
"""

from __future__ import annotations

from tckdb_schemas.fragments.calculation import (
    W_FREQ_MODE_INDEX_NOT_UNIQUE,
    W_FREQ_N_IMAG_DISAGREES_WITH_MODES,
)

_ROUTE = "/api/v1/uploads/conformers"
_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}

#: Water, non-linear: 3 atoms, 3N - 6 = 3 vibrational modes. Chosen so a
#: complete list is three rows long, which keeps the accepted half free of
#: the completeness warning and the assertion on ``warnings == []`` honest.
_WATER_XYZ = (
    "3\nwater\n"
    "O  0.000000  0.000000  0.117300\n"
    "H  0.000000  0.757200 -0.469200\n"
    "H  0.000000 -0.757200 -0.469200"
)
_WATER_SPECTRUM = [1595.0, 3657.0, 3756.0]


def _payload(*, indices: list[int], label: str, n_imag: int = 0) -> dict:
    """A water conformer whose freq result numbers its modes as given.

    ``indices`` is the only thing that varies between the refused and the
    accepted half: same species, same geometry, same three frequencies,
    same everything the workflow touches after validation.
    """
    modes = [
        {
            "mode_index": index,
            "frequency_cm1": frequency,
            "is_imaginary": frequency < 0,
        }
        for index, frequency in zip(indices, _WATER_SPECTRUM, strict=True)
    ]
    return {
        "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
        "geometry": {"xyz_text": _WATER_XYZ},
        "calculation": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "additional_calculations": [
            {
                "type": "freq",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_result": {
                    "n_imag": n_imag,
                    "zpe_hartree": 0.02,
                    "modes": modes,
                },
            }
        ],
        "label": label,
    }


def test_a_repeated_mode_index_is_named_on_the_wire(client) -> None:
    """The refusal reaches the ``code`` field, not just the prose."""
    resp = client.post(
        _ROUTE, json=_payload(indices=[1, 2, 2], label="dup-index")
    )
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == W_FREQ_MODE_INDEX_NOT_UNIQUE, body
    assert body["context"] == {
        "field": "modes",
        "duplicate_mode_indices": [2],
        "mode_count": 3,
    }, body


def test_the_published_sentence_is_unmoved(client) -> None:
    """Attaching a code was additive: a prose-matching client is undisturbed.

    Separate from the code assertion because they can fail
    independently, and a rewrite of the message is a breaking change to a
    published contract while a new code is not.
    """
    resp = client.post(
        _ROUTE, json=_payload(indices=[1, 1, 3], label="dup-prose")
    )
    assert resp.status_code == 422, resp.text[:800]
    assert "mode_index values must be unique within a freq result." in str(
        resp.json()["detail"]
    )


def test_a_correctly_numbered_frequency_list_is_still_accepted(client) -> None:
    """The negative half. Same three modes, numbered 1-2-3."""
    resp = client.post(
        _ROUTE, json=_payload(indices=[1, 2, 3], label="unique-index")
    )
    assert resp.status_code == 201, resp.text[:800]
    assert resp.json()["warnings"] == [], resp.text[:800]


def _two_bad_freq_calculations() -> dict:
    """A conformer whose *two* freq results each repeat a mode index.

    Deliberately different in both facts they report — ``[2]`` in a
    three-mode list against ``[7]`` in a two-mode one — so that a context
    built by merging the two is not merely wrong but says which of them
    it kept.
    """
    payload = _payload(indices=[1, 2, 2], label="two-dup-index")
    second = {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "freq_result": {
            "n_imag": 0,
            "zpe_hartree": 0.02,
            "modes": [
                {"mode_index": 7, "frequency_cm1": 1595.0, "is_imaginary": False},
                {"mode_index": 7, "frequency_cm1": 3657.0, "is_imaginary": False},
            ],
        },
    }
    payload["additional_calculations"].append(second)
    return payload


def test_two_repeated_mode_indices_report_no_single_code_and_no_facts(
    client,
) -> None:
    """#236, on the wire: ``code`` and ``context`` must agree.

    A request that fails twice is two things to fix, so the envelope
    declines to name one — ``validation_detail_code`` has always fallen
    back on more than one failure, whatever codes they carry.
    ``validation_detail_context`` used to fall back only on more than one
    *distinct* code, so these two failures (same code, twice) produced
    the generic code beside a populated ``context``: the facts of the
    second freq result, ``{"duplicate_mode_indices": [7], "mode_count":
    2}``, attached to a code naming neither result, with the first
    result's ``[2]``/``3`` silently overwritten by ``merged.update()``
    and reported nowhere.

    ``detail`` still carries both failures in full — only the
    single-failure summary is withheld, which is the whole of the
    decision.
    """
    resp = client.post(_ROUTE, json=_two_bad_freq_calculations())
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()

    assert body["code"] == "request_validation_error", body
    assert body["context"] == {}, (
        "facts about one failure reached a client under a code naming "
        f"neither failure: {body['context']}"
    )
    # The premise: this really is the two-failure case, not a payload
    # that happened to be refused once. Without it the assertions above
    # would also pass on a single-failure request.
    assert len(body["detail"]) == 2, body["detail"]
    assert all(
        "mode_index values must be unique within a freq result." in str(entry)
        for entry in body["detail"]
    ), body["detail"]


def test_one_repeated_mode_index_still_names_the_code_and_the_facts(
    client,
) -> None:
    """The other direction of the test above, one failure instead of two.

    Same route, same builder, same refusal. Without this, a
    ``validation_detail_context`` that returned ``{}`` unconditionally --
    or a route that stopped promoting codes at all -- would leave the
    two-failure assertions green while the envelope told a client
    nothing.
    """
    payload = _two_bad_freq_calculations()
    payload["additional_calculations"] = payload["additional_calculations"][:1]

    resp = client.post(_ROUTE, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()

    assert body["code"] == W_FREQ_MODE_INDEX_NOT_UNIQUE, body
    assert body["context"] == {
        "field": "modes",
        "duplicate_mode_indices": [2],
        "mode_count": 3,
    }, body
    assert len(body["detail"]) == 1, body["detail"]


def test_out_of_order_indices_are_not_duplicates(client) -> None:
    """Order is not the rule; uniqueness is.

    A list arriving 3-1-2 is unusual but says which row is which mode,
    which is the whole property. Pinned because "sorted ascending" is the
    tempting stronger rule, and it would refuse a correct deposit.
    """
    resp = client.post(
        _ROUTE, json=_payload(indices=[3, 1, 2], label="shuffled-index")
    )
    assert resp.status_code == 201, resp.text[:800]


def test_the_neighbouring_disagreement_keeps_its_own_code(client) -> None:
    """One code per repair: the two refusals in this validator stay apart.

    ``n_imag`` disagreeing with the modes is a different thing to fix
    from a list that numbers two rows the same, and a client branches on
    which. Asserting it here guards the specific regression of a new code
    swallowing the older one when both checks live in one validator.
    """
    assert W_FREQ_MODE_INDEX_NOT_UNIQUE != W_FREQ_N_IMAG_DISAGREES_WITH_MODES
    resp = client.post(
        _ROUTE, json=_payload(indices=[1, 2, 3], label="n-imag-clash", n_imag=2)
    )
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == W_FREQ_N_IMAG_DISAGREES_WITH_MODES
