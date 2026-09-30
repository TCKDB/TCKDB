"""A warning must name a field the depositor can actually fill.

``collect_kinetics_content_warnings`` reads ``interpretation_assignments``,
``network_kinetics_ref`` and ``tunneling_application``.
``BundleKineticsIn`` — the reaction bundle's kinetics block, and the model
the ARC adapter deposits through — has none of the three, but *does* have
``tunneling_model``. Wired naively to the bundle route, it would answer a
bundle declaring ``tunneling_model='eckart'`` with "supply
``tunneling_application``": a field that does not exist on that model, on
a payload whose ``SchemaBase`` is ``extra="forbid"``. Following the advice
yields a 422.

The fix is the sentinel ``provenance_warnings.NOT_APPLICABLE``, the same
device PR #155 used for ``energy_level_of_theory``: ``None`` means *there
is a field and it is empty*, ``NOT_APPLICABLE`` means *there is no field
here*, and only the first is judged.

Two levels of test, because they are different claims:

* The route level asserts what a depositor **receives** — the only thing
  that matters to them, and the thing a unit test on the collector cannot
  establish.
* The unit level asserts the sentinel is **doing the work**. Without it,
  the route test would also pass on ``main`` (where the collector is
  simply never called on this route), so it would prove nothing about
  this change.

#620 added the three fields to ``BundleKineticsIn``. The reaction route still
does not call the collector, so a bundle is not told about a missing
interpretation set: the advice would ask for a ref to a record the same
request cannot have created yet. The sentinel remains the way a payload
without the field opts out of the advice.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.provenance_warnings import (
    NOT_APPLICABLE,
    W_MISSING_KINETICS_INTERPRETATIONS,
    W_MISSING_TS_INTERPRETATION,
    W_MISSING_TUNNELING_APPLICATION,
    collect_kinetics_content_warnings_for,
)

#: The codes that name a field ``BundleKineticsIn`` does not have.
UNACTIONABLE_ON_THE_BUNDLE = frozenset(
    {
        W_MISSING_KINETICS_INTERPRETATIONS,
        W_MISSING_TS_INTERPRETATION,
        W_MISSING_TUNNELING_APPLICATION,
    }
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"


def _species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {
            "smiles": smiles,
            "charge": 0,
            "multiplicity": multiplicity,
        },
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }


def _bundle_with_tunnelling_label() -> dict:
    return {
        "species": [
            _species("h", "[H]", 2, _XYZ_H),
            _species("h2", "[H][H]", 1, _XYZ_H2),
        ],
        "reversible": True,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
        "kinetics": [
            {
                "scientific_origin": "computed",
                "model_kind": "arrhenius",
                "a": 1.0e13,
                "a_units": "cm3_mol_s",
                "n": 0.0,
                "reported_ea": 10.0,
                "reported_ea_units": "kj_mol",
                "tmin_k": 300.0,
                "tmax_k": 2000.0,
                "reactant_keys": ["h", "h"],
                "product_keys": ["h2"],
                # The claim the bundle can make and cannot evidence.
                "tunneling_model": "eckart",
            }
        ],
    }


# ---------------------------------------------------------------------------
# What a depositor receives
# ---------------------------------------------------------------------------


def test_a_bundle_declaring_tunneling_is_not_told_to_fill_a_missing_field(
    client: TestClient,
):
    """The end-to-end claim: no un-actionable code on the 201.

    Asserted as "none of these three codes appear" rather than "the
    warning list is empty", because the bundle route legitimately returns
    other warnings (provenance) and this must not become a test that
    passes only while the response happens to be quiet.
    """
    resp = client.post(
        "/api/v1/uploads/computed-reaction", json=_bundle_with_tunnelling_label()
    )
    assert resp.status_code == 201, resp.text[:800]

    codes = {w["code"] for w in resp.json()["warnings"]}
    offending = codes & UNACTIONABLE_ON_THE_BUNDLE
    assert not offending, (
        f"the reaction bundle told a depositor to supply {sorted(offending)}, "
        "which name fields BundleKineticsIn does not have; SchemaBase is "
        "extra='forbid', so acting on that advice is a 422"
    )


def test_the_bundle_route_now_validates_a_tunneling_application_block(
    client: TestClient,
):
    """``tunneling_application`` is a real bundle field since #620.

    Until then it was rejected as an unknown key (``SchemaBase`` is
    ``extra="forbid"``), which is why the collector was told the field did not
    exist. Now the block is parsed and held to the standalone route's rules:
    an Eckart block with no barriers is refused for *that* reason, not as an
    unknown field.
    """
    bundle = _bundle_with_tunnelling_label()
    bundle["kinetics"][0]["tunneling_application"] = {
        "model": "eckart",
        "transition_state_entry_ref": "ts-1",
    }

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 422, resp.text[:600]
    assert "Extra inputs are not permitted" not in resp.text
    assert "Wigner/Eckart tunneling requires imaginary_frequency_cm1" in resp.text


# ---------------------------------------------------------------------------
# That the sentinel is what is doing it
# ---------------------------------------------------------------------------


def test_not_applicable_suppresses_exactly_what_none_would_raise():
    """The sentinel must differ from ``None``, or it is decoration.

    Same origin, same declared ``tunneling_model``, same everything —
    only the three inputs change from "absent field" to "empty field".
    The ``None`` call is the control: it shows the collector *would* have
    produced the un-actionable advice, so the empty result above is the
    sentinel working rather than the collector having nothing to say.
    """
    as_bundle = collect_kinetics_content_warnings_for(
        scientific_origin="computed",
        interpretation_assignments=NOT_APPLICABLE,
        network_kinetics_ref=NOT_APPLICABLE,
        tunneling_model=_eckart(),
        tunneling_application=NOT_APPLICABLE,
    )
    assert [w.code for w in as_bundle] == []

    as_standalone = collect_kinetics_content_warnings_for(
        scientific_origin="computed",
        interpretation_assignments=[],
        network_kinetics_ref=None,
        tunneling_model=_eckart(),
        tunneling_application=None,
    )
    assert {w.code for w in as_standalone} == {
        W_MISSING_KINETICS_INTERPRETATIONS,
        W_MISSING_TUNNELING_APPLICATION,
    }


def test_judging_is_opt_in_so_a_new_caller_cannot_leak_advice():
    """The defaults are the safe direction.

    A caller that passes only what it has must not be judged on what it
    does not — otherwise the next route wired to this collector
    reintroduces the defect by omission, which is exactly how it arrived.
    """
    warnings = collect_kinetics_content_warnings_for(
        scientific_origin="computed",
        tunneling_model=_eckart(),
    )
    assert [w.code for w in warnings] == []


def test_a_caller_that_has_the_fields_is_still_fully_judged():
    """The standalone route must be unchanged by the refactor.

    The risk in making judging opt-in is that a real gap stops being
    reported. This pins the standalone adapter's behaviour against the
    parameterised core.
    """
    from app.services.provenance_warnings import collect_kinetics_content_warnings

    class _Req:
        scientific_origin = "computed"
        interpretation_assignments: list = []
        network_kinetics_ref = None
        tunneling_model = _eckart()
        tunneling_application = None

    codes = {w.code for w in collect_kinetics_content_warnings(_Req())}
    assert codes == {
        W_MISSING_KINETICS_INTERPRETATIONS,
        W_MISSING_TUNNELING_APPLICATION,
    }


def _eckart():
    from app.db.models.common import TunnelingModel

    return TunnelingModel.eckart
