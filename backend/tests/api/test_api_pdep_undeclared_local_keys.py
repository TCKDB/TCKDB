"""Five wire-reachable 500s on ``/uploads/networks/pdep``, and their codes.

What was broken
---------------
Every one of these was a depositor typing a name wrong in their own
payload and the server answering ``500 Internal Server Error`` — the
server blaming itself for the user's mistake, with nothing in the body
saying which key was wrong. Each was an unhandled ``KeyError`` out of the
route: no ``KeyError`` handler is registered in
``app.api.errors.register_exception_handlers``, so it reaches Starlette
as an unhandled exception.

Why the schema did not already stop them
----------------------------------------
Two different holes, and neither is the "the guard lives in another
package" story that :mod:`app.services.local_key_resolution` was written
for. All four validators involved live in ``backend/app/schemas``, beside
the workflow.

**Four of the five are conditional guards.** A solve's
``state_energies[].state_key`` and its ``channel_barriers[]`` channel,
micro-reaction and transition-state keys are only ever checked inside the
``kind == 'computed'`` branch of
``NetworkPDepUploadRequest.validate_mechanistic_channel_evidence``, where
the coverage rules compare supplied keys against the network topology and
reject an undefined one as a side effect. ADR 0010's ``reported`` solve —
k(T,P) transcribed out of a published table, holding none of the
master-equation inputs — is exempt from those coverage rules, and was
therefore exempt from the side effect too. The comment above that branch
says a reported solve "still has to point it at a real path"; for three of
these four fields, it did not.

**The fifth is the #218 asymmetry, on a field where it was already
half-known.** ``NetworkSpeciesIn.validate_calc_geometry_belongs_to_
conformer`` narrows a species calculation's ``geometry_key`` to that
species's own conformer geometries. ``TransitionStateIn`` has no
counterpart, and ``validate_key_references`` checks TS calculations
against the *global* geometry namespace.

That much was already on record: ``tests/services/test_calculation_
geometry_composition.py::test_a_ts_calculation_may_not_borrow_a_
reactants_geometry_by_key`` reproduces it on
``/uploads/computed-reaction`` and pins the refusal that closed it —
``calculation_geometry_composition_mismatch``, which catches the TS
carrying the wrong *atoms*.

What that defence cannot do is help when the key does not resolve at
all, because it runs **downstream of the lookup**. On the PDep route the
geometry map is filled as the workflow walks the transition states in
order, so a TS calculation naming a *later* TS's geometry raised
``KeyError`` before any composition check saw it. Same asymmetry, one
layer earlier, and a 500 instead of a 422.
``test_resolving_a_geometry_key_does_not_bypass_the_composition_rule``
below pins the handoff: once the key resolves, the composition rule
still owns the question of whether the geometry is the right molecule.

How these assertions are written
--------------------------------
On the ``(status, code)`` pair, never on one alone — asserting only the
status passes against any refusal, and asserting only the code lets the
status drift to something a client retries differently. Never on a
substring of ``detail``: Pydantic echoes rejected input back into its own
error strings, so a substring check passes even where the field was
wrongly *accepted*.

The accept-side tests at the bottom are what stop the rest from being
vacuous. A resolver that refused every key would satisfy every negative
assertion in this file and fail every one of those.
"""

from __future__ import annotations

from tests.workflows.test_network_pdep_upload import (
    _CONVENTIONS,
    _parallel_path_payload,
    _reported_payload,
)

_PDEP_URL = "/api/v1/uploads/networks/pdep"


def _post(client, payload: dict):
    return client.post(_PDEP_URL, json=payload)


def _assert_refused(response, *, code: str) -> dict:
    """Pin the pair. Neither half is load-bearing on its own."""
    assert (response.status_code, response.json().get("code")) == (422, code), (
        f"expected (422, {code!r}), got "
        f"({response.status_code}, {response.json().get('code')!r}). "
        f"body={response.text}"
    )
    return response.json()


def _assert_no_row_ids(body: dict) -> None:
    """DR-0028 Requirement 2 -- the maps' *values* are row ids."""
    assert set(body["context"]) == {"field", "key", "declared_keys"}, body
    assert all(isinstance(k, str) for k in body["context"]["declared_keys"]), body


def _barrier(**over) -> dict:
    base = {
        "channel_key": "elimination_path",
        "micro_reaction_key": "rxn_ho2_elim",
        "transition_state_key": "ts_elim",
        "forward_barrier_kj_mol": 10.0,
        "reverse_barrier_kj_mol": 20.0,
        **_CONVENTIONS,
    }
    base.update(over)
    return base


def _state_energy(**over) -> dict:
    base = {"state_key": "well_RO2", "energy_kj_mol": -120.0, **_CONVENTIONS}
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# The four conditional-guard defects, all on a `reported` solve
# ---------------------------------------------------------------------------


def test_reported_solve_state_energy_naming_no_state_is_a_coded_422(client) -> None:
    payload = _reported_payload()
    payload["solve"]["state_energies"] = [_state_energy(state_key="no_such_state")]
    body = _assert_refused(_post(client, payload), code="network_state_key_undeclared")
    assert body["context"]["field"] == "solve.state_energies[0].state_key", body
    assert body["context"]["key"] == "no_such_state", body
    # Naming the alternatives is what turns a typo into a mechanical fix.
    assert "well_RO2" in body["context"]["declared_keys"], body
    _assert_no_row_ids(body)


def test_reported_solve_barrier_naming_no_channel_is_a_coded_422(client) -> None:
    payload = _reported_payload()
    payload["solve"]["channel_barriers"] = [_barrier(channel_key="no_such_channel")]
    body = _assert_refused(
        _post(client, payload), code="network_channel_key_undeclared"
    )
    assert body["context"]["field"] == "solve.channel_barriers[0].channel_key", body
    assert body["context"]["key"] == "no_such_channel", body
    assert "elimination_path" in body["context"]["declared_keys"], body
    _assert_no_row_ids(body)


def test_reported_solve_barrier_naming_no_micro_reaction_is_a_coded_422(
    client,
) -> None:
    payload = _reported_payload()
    payload["solve"]["channel_barriers"] = [_barrier(micro_reaction_key="no_such_rxn")]
    body = _assert_refused(_post(client, payload), code="micro_reaction_key_undeclared")
    assert (
        body["context"]["field"] == "solve.channel_barriers[0].micro_reaction_key"
    ), body
    assert body["context"]["key"] == "no_such_rxn", body
    assert "rxn_ho2_elim" in body["context"]["declared_keys"], body
    _assert_no_row_ids(body)


def test_reported_solve_barrier_naming_no_transition_state_is_a_coded_422(
    client,
) -> None:
    payload = _reported_payload()
    payload["solve"]["channel_barriers"] = [_barrier(transition_state_key="no_such_ts")]
    body = _assert_refused(
        _post(client, payload), code="transition_state_key_undeclared"
    )
    assert (
        body["context"]["field"] == "solve.channel_barriers[0].transition_state_key"
    ), body
    assert body["context"]["key"] == "no_such_ts", body
    assert "ts_elim" in body["context"]["declared_keys"], body
    _assert_no_row_ids(body)


def test_the_four_solve_keys_do_not_share_one_code(client) -> None:
    """Four namespaces, four codes -- the point of not writing one.

    A client pointing a depositor at the block of their own payload that
    needs editing can only do that if the code says which block. If these
    ever collapse to a single ``local_key_undeclared`` a client has to
    parse ``context['field']`` to tell a state key from a channel key.
    """
    codes = set()
    for solve_over in (
        {"state_energies": [_state_energy(state_key="ghost")]},
        {"channel_barriers": [_barrier(channel_key="ghost")]},
        {"channel_barriers": [_barrier(micro_reaction_key="ghost")]},
        {"channel_barriers": [_barrier(transition_state_key="ghost")]},
    ):
        payload = _reported_payload()
        payload["solve"].update(solve_over)
        response = _post(client, payload)
        assert response.status_code == 422, response.text
        codes.add(response.json()["code"])
    assert len(codes) == 4, codes


# ---------------------------------------------------------------------------
# The geometry defect: the species branch is guarded, the TS branch was not
# ---------------------------------------------------------------------------


def test_ts_calculation_naming_a_later_ts_geometry_is_a_coded_422(client) -> None:
    """The #218 asymmetry, found again on ``geometry_key``.

    ``_parallel_path_payload`` declares three saddle points in the order
    ``ts_elim, ts_elim_anti, ts_isomer``. The first one's ``sp``
    calculation is pointed at the third one's geometry: a key the payload
    really does declare, and one this workflow cannot resolve when it
    persists ``ts_elim``.
    """
    payload = _parallel_path_payload()
    assert [ts["key"] for ts in payload["transition_states"]] == [
        "ts_elim",
        "ts_elim_anti",
        "ts_isomer",
    ], "fixture TS order changed; this test depends on it"
    payload["transition_states"][0]["calculations"][1]["geometry_key"] = (
        "ts_isomer_geom"
    )
    body = _assert_refused(_post(client, payload), code="geometry_key_unresolved")
    assert body["context"]["field"] == "calculations['ts_elim_sp'].geometry_key", body
    assert body["context"]["key"] == "ts_isomer_geom", body
    _assert_no_row_ids(body)


def test_the_geometry_refusal_does_not_claim_the_key_was_undeclared(client) -> None:
    """The reason this code is not spelled ``geometry_key_undeclared``.

    ``ts_isomer_geom`` is in the payload the depositor sent. A refusal
    saying it was never declared would be telling them something false
    about their own file, and would send them looking for a typo that is
    not there.
    """
    payload = _parallel_path_payload()
    payload["transition_states"][0]["calculations"][1]["geometry_key"] = (
        "ts_isomer_geom"
    )
    detail = _post(client, payload).json()["detail"]
    assert "does not name a geometry declared in this upload" not in detail, detail
    assert "has resolved at this point" in detail, detail
    # And it says what would work instead.
    assert "ts_elim_geom" in detail, detail


# ---------------------------------------------------------------------------
# The half that makes all of the above mean something
# ---------------------------------------------------------------------------


def test_a_reported_solve_with_every_key_right_is_still_accepted(client) -> None:
    """A resolver that refused everything passes all five tests above."""
    payload = _reported_payload()
    payload["solve"]["state_energies"] = [_state_energy()]
    payload["solve"]["channel_barriers"] = [_barrier()]
    response = _post(client, payload)
    assert response.status_code == 201, response.text


def test_a_ts_calculation_naming_its_own_geometry_is_still_accepted(client) -> None:
    """The geometry resolver resolves; it does not merely refuse."""
    response = _post(client, _parallel_path_payload())
    assert response.status_code == 201, response.text


def test_a_ts_calculation_may_still_name_an_already_resolved_geometry(client) -> None:
    """Species geometries are all resolved before any TS is persisted.

    The control for the ``ts_isomer_geom`` test: it proves the refusal
    there is about resolution order, not a blanket ban on a TS
    calculation citing a geometry it does not own.

    ``etoo_geom`` is ethylperoxy and ``ts_elim`` is the C2H5O2 saddle
    point, so the two agree on composition. That is deliberate and it is
    what isolates *resolution* from the separate composition rule
    exercised in the next test -- borrowing a geometry of the wrong
    composition is refused, and this one is not being accepted because
    the rule was skipped.
    """
    payload = _parallel_path_payload()
    payload["transition_states"][0]["calculations"][1]["geometry_key"] = "etoo_geom"
    response = _post(client, payload)
    assert response.status_code == 201, response.text


def test_resolving_a_geometry_key_does_not_bypass_the_composition_rule(
    client,
) -> None:
    """The geometry resolver hands off; it does not launder.

    `calculation_geometry_composition_mismatch` is the existing,
    scientific guard against a calculation carrying a geometry made of
    the wrong atoms -- and on `/uploads/computed-reaction` it is what
    already caught a TS calculation borrowing a reactant's geometry
    (`tests/services/test_calculation_geometry_composition.py::
    test_a_ts_calculation_may_not_borrow_a_reactants_geometry_by_key`).

    That guard sits *downstream* of the key lookup, which is exactly why
    it could not defend the PDep 500 fixed above: a key that never
    resolves never reaches it. Now that the key resolves or is refused by
    code, this test pins the other half -- a geometry that resolves and
    is still the wrong molecule is still refused, by the rule that owns
    that question, and not silently accepted because a `KeyError` turned
    into a return value.

    `ethyl_geom` is C2H5; `ts_elim` is the C2H5O2 saddle point.
    """
    payload = _parallel_path_payload()
    payload["transition_states"][0]["calculations"][1]["geometry_key"] = "ethyl_geom"
    response = _post(client, payload)
    assert (response.status_code, response.json().get("code")) == (
        422,
        "calculation_geometry_composition_mismatch",
    ), response.text
