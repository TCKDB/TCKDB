"""``bac_total`` with no components, measured on the wire (task #264).

What was wrong
---------------
The live deployment carries 19 ``applied_energy_correction`` rows with
``application_role='bac_total'``, ``value=0``, zero
``applied_energy_correction_component`` children, and no note. Two target
a monatomic species (``[H]``, ``[O]``) and are honest: an atom has no
bonds, so a bond-additivity total of zero is true and needs no
components. Seventeen target a ``transition_state_entry`` and are false:
a bond-additivity correction is definitionally a sum over bonds, a
saddle point carries no bond assignment at all (Arkane's own
Petersson-BAC routine sums whatever bond dictionary it is handed, and
ARC supplies none for a TS), and the reactant/product species of those
same reactions carry real BACs of -3 to -16 kcal/mol -- so a corrected
barrier height built from the stored numbers is biased by the reactants'
correction alone.

``create_applied_energy_correction`` stored exactly what a depositor
sent (and still does -- see its own docstring); nothing on the upload
wire path refused the shape. Before
``assert_bac_total_has_required_components`` existed, every refusal
test in this file was measured to return 201 for its payload (see the
PR description for the mutation-check log); each now pins the 422 the
fix produces, paired with an acceptance test so the guard cannot be
satisfied by refusing everything.

Why these tests are on the wire, not at the model
--------------------------------------------------
The rule lives in
``app.services.energy_correction_resolution.assert_bac_total_has_required_components``
and is exercised by four workflows. What a depositor's client actually
receives is the ``(status, code)`` pair the HTTP layer renders, so this
file posts to ``/uploads/computed-reaction`` -- the one route that can
target both a species entry and a transition state entry in a single
bundle -- rather than calling the workflow function directly.
"""

from __future__ import annotations

from tests.workflows.test_computed_reaction_upload import (
    _bac_melius_scheme_ref_rxn,
    _bac_petersson_scheme_ref_rxn,
    _payload_with_aec_carriers,
)

_URL = "/api/v1/uploads/computed-reaction"
_CODE = "bac_total_requires_components"


def _bac(scheme: dict, *, value: float, source_calculation_key: str, components=None) -> dict:
    payload = {
        "scheme": scheme,
        "application_role": "bac_total",
        "value": value,
        "value_unit": "hartree",
        "source_calculation_key": source_calculation_key,
    }
    if components is not None:
        payload["components"] = components
    return payload


def test_ts_componentless_bac_total_is_refused(client) -> None:
    """The 17-row shape: TS target, bac_petersson, zero value, no components."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        _bac(_bac_petersson_scheme_ref_rxn(), value=0.0, source_calculation_key="ts-sp")
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _CODE, body
    assert body["context"]["target_kind"] == "transition_state_entry", body
    assert "transition_state.applied_energy_corrections[0]" in body["context"]["field"], body


def test_ts_componentless_bac_total_is_refused_even_with_a_nonzero_value(client) -> None:
    """The rule is not a ``value == 0`` special case.

    A componentless TS-side BAC is refused regardless of the number
    attached to it: "no bond assignment exists for a TS" does not
    become more provable at a different value.
    """
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        _bac(_bac_petersson_scheme_ref_rxn(), value=-0.37, source_calculation_key="ts-sp")
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _CODE, body
    assert body["context"]["target_kind"] == "transition_state_entry", body


def test_ts_bac_total_with_a_placeholder_component_is_still_refused(client) -> None:
    """A component of the wrong kind does not satisfy the contract.

    Only a ``component_kind='bond'`` component proves anything about
    bonds having been summed. A single ``other``/``unspecified``
    placeholder -- exactly what would make ``if payload.components``
    alone pass -- re-admits the same false shape with the contract's
    blessing, so it must still be refused.
    """
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        _bac(
            _bac_petersson_scheme_ref_rxn(),
            value=0.0,
            source_calculation_key="ts-sp",
            components=[
                {
                    "component_kind": "other",
                    "key": "unspecified",
                    "multiplicity": 1,
                    "parameter_value": 0.0,
                    "contribution_value": 0.0,
                }
            ],
        )
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _CODE, body
    assert body["context"]["target_kind"] == "transition_state_entry", body


def test_ts_bac_total_with_components_is_accepted(client) -> None:
    """The positive half: a TS-side BAC that states its bonds is accepted.

    A producer that genuinely applied a BAC to a transition state
    supplies the bonds it used, and that deposit must still succeed.
    """
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        _bac(
            _bac_petersson_scheme_ref_rxn(
                bond_params=[{"bond_key": "C-H", "value": -0.11}],
            ),
            value=-0.66,
            source_calculation_key="ts-sp",
            components=[
                {
                    "component_kind": "bond",
                    "key": "C-H",
                    "multiplicity": 6,
                    "parameter_value": -0.11,
                    "contribution_value": -0.66,
                }
            ],
        )
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 201, resp.text[:800]


def test_polyatomic_species_componentless_bac_total_is_refused(client) -> None:
    """The species-side analogue: ``[CH3]`` has bonds, so this is refused too."""
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        _bac(_bac_petersson_scheme_ref_rxn(), value=0.0, source_calculation_key="ch3-sp")
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _CODE, body
    assert body["context"]["target_kind"] == "species_entry", body
    assert body["context"]["field"] == "species['ch3'].applied_energy_corrections[0]", body


def test_monatomic_species_componentless_bac_total_is_accepted(client) -> None:
    """The honest half: ``[H]`` has no bonds, so a componentless zero stands.

    Mirrors the two legitimate live rows this task's migration leaves
    untouched.
    """
    payload = _payload_with_aec_carriers()
    payload["species"][1]["applied_energy_corrections"] = [
        _bac(_bac_petersson_scheme_ref_rxn(), value=0.0, source_calculation_key="h-sp")
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 201, resp.text[:800]


def test_bac_melius_componentless_species_total_is_still_accepted(client) -> None:
    """Scope check: the rule is Petersson-only.

    A Melius BAC total does not decompose into a stable per-bond
    breakdown even for a bonded species (see
    ``test_bac_melius_no_components_persists`` in the species-bundle
    workflow tests), so a componentless Melius total on a bonded species
    proves nothing about whether bonds were summed and must continue to
    be accepted.
    """
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        _bac(_bac_melius_scheme_ref_rxn(), value=-0.04, source_calculation_key="ch3-sp")
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 201, resp.text[:800]


def test_bac_melius_componentless_ts_total_is_still_accepted(client) -> None:
    """Scope check, TS side: same Melius exemption applies here too."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        _bac(_bac_melius_scheme_ref_rxn(), value=-0.02, source_calculation_key="ts-sp")
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 201, resp.text[:800]


def test_no_row_id_in_the_refusal(client) -> None:
    """The 422 body names a field path and a target kind, never a row id."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        _bac(_bac_petersson_scheme_ref_rxn(), value=0.0, source_calculation_key="ts-sp")
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    for value in (str(body.get("detail", "")), str(body.get("context", {}))):
        assert "species_entry_id" not in value, body
        assert "transition_state_entry_id" not in value, body
