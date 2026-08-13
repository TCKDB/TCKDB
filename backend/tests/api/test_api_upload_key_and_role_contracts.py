"""A local key must name something the upload declared, and a role must fit.

Two contracts, both about the same failure mode: an upload naming
provenance it does not have, and getting away with it.

* **A declared role must match the calculation's type.** Thermo has
  enforced this since DR-0028 Requirement 1
  (``app.workflows.thermo.assert_thermo_role_matches_calculation_type``).
  Statmech did not, on any of its three write paths, so
  ``{"calculation_key": "my_sp_job", "role": "freq"}`` persisted happily
  and the statmech record then claimed a frequency calculation it never
  had.

* **With exactly one exception: an ``opt`` may serve the ``sp`` role.**
  A depositor who ran no separate single point has not lost the number —
  the optimisation's final energy *is* the single-point value, at the
  optimisation's own level of theory. The exception does not run in
  reverse and does not generalise; the last section here pins both
  halves of that, because "restoring symmetry" should cost a red test
  rather than a rejected deposit.

* **A declared key must name something declared.** The conformer upload's
  applied energy corrections tested both of their source keys for
  ``is not None`` and resolved either one to the primary
  observation/calculation whatever it said — a key that cannot be wrong
  is not a key, it is a boolean spelled as a string.

The assertions here are on the envelope's ``code`` field, never on
substring presence in ``detail``. That is not stylistic: Pydantic echoes
rejected input back into its error string, so ``assert "role" in
resp.text`` is true even when the field was wrongly *accepted*. Only
``body["code"] == ...`` distinguishes a refusal from an echo.
"""

from __future__ import annotations

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}

_ROLE_TYPE_MISMATCH = "statmech_source_role_type_mismatch"
_THERMO_ROLE_TYPE_MISMATCH = "thermo_source_role_type_mismatch"
_KEY_UNDECLARED = "applied_energy_correction_source_key_undeclared"


def _assert_code(response, expected: str) -> dict:
    """The response is a 422 that names *expected* in its ``code`` field."""
    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) >= {"code", "detail"}, body
    assert body["code"] == expected, (
        f"expected code={expected!r}, got {body['code']!r}. "
        f"detail={body['detail']!r}"
    )
    return body


def _sp_calc() -> dict:
    return {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "sp_result": {"electronic_energy_hartree": -76.437},
    }


def _freq_calc() -> dict:
    return {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "freq_result": {"n_imag": 0, "zpe_hartree": 0.021},
    }


def _opt_calc() -> dict:
    return {
        "type": "opt",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "opt_result": {"converged": True},
    }


# ---------------------------------------------------------------------------
# Path 1 of 3: the standalone statmech upload
# ---------------------------------------------------------------------------


def _standalone_statmech_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "CO", "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
    }
    base.update(overrides)
    return base


class TestStandaloneStatmechRoleMatchesType:
    def test_freq_role_on_an_sp_calculation_is_refused(self, client):
        payload = _standalone_statmech_payload(
            calculations=[{"key": "my_sp_job", "calculation": _sp_calc()}],
            source_calculations=[
                {"calculation_key": "my_sp_job", "role": "freq"}
            ],
        )
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        body = _assert_code(resp, _ROLE_TYPE_MISMATCH)
        # The offending key is named; no row id is disclosed.
        assert body["context"]["declared_role"] == "freq"
        assert body["context"]["actual_calculation_type"] == "sp"
        assert body["context"]["expected_calculation_type"] == "freq"

    def test_matching_role_still_uploads(self, client):
        """The green half: the same shape with the role told truthfully."""
        payload = _standalone_statmech_payload(
            species_entry={"smiles": "CCO", "charge": 0, "multiplicity": 1},
            calculations=[
                {"key": "my_sp_job", "calculation": _sp_calc()},
                {"key": "my_freq_job", "calculation": _freq_calc()},
            ],
            source_calculations=[
                {"calculation_key": "my_sp_job", "role": "sp"},
                {"calculation_key": "my_freq_job", "role": "freq"},
            ],
        )
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        assert resp.status_code == 201, resp.text

    def test_imported_role_accepts_any_type(self, client):
        """``imported`` names an origin, not a job type — as for thermo."""
        payload = _standalone_statmech_payload(
            species_entry={"smiles": "CCC", "charge": 0, "multiplicity": 1},
            calculations=[{"key": "my_sp_job", "calculation": _sp_calc()}],
            source_calculations=[
                {"calculation_key": "my_sp_job", "role": "imported"}
            ],
        )
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Path 2 of 3: statmech nested under a conformer upload
# ---------------------------------------------------------------------------


def _conformer_payload(label: str, **overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": dict(_sp_calc(), key="my_sp_job"),
        "label": label,
        "note": "key/role contract test",
    }
    base.update(overrides)
    return base


class TestNestedStatmechRoleMatchesType:
    def test_uploaded_calculation_role_must_match_the_uploaded_calc(
        self, client
    ):
        payload = _conformer_payload(
            "conf-role-implicit",
            statmech={"uploaded_calculation_role": "freq"},
        )
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        body = _assert_code(resp, _ROLE_TYPE_MISMATCH)
        assert body["context"]["field"] == "statmech.uploaded_calculation_role"

    def test_source_calculation_role_must_match_the_key_it_names(self, client):
        payload = _conformer_payload(
            "conf-role-explicit",
            statmech={
                "source_calculations": [
                    {"calculation_key": "my_sp_job", "role": "freq"}
                ]
            },
        )
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        _assert_code(resp, _ROLE_TYPE_MISMATCH)

    def test_truthful_role_still_uploads(self, client):
        payload = _conformer_payload(
            "conf-role-ok",
            statmech={"uploaded_calculation_role": "sp"},
        )
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Path 3 of 3: statmech inside a computed-species bundle
# ---------------------------------------------------------------------------


def _bundle_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "level_of_theory": _LOT,
                    "software_release": _SOFTWARE,
                    "opt_result": {"converged": True},
                },
            }
        ],
    }
    base.update(overrides)
    return base


class TestBundleStatmechRoleMatchesType:
    def test_freq_role_on_an_opt_calculation_is_refused(self, client):
        payload = _bundle_payload(
            statmech={
                "external_symmetry": 1,
                "source_calculations": [
                    {"calculation_key": "opt0", "role": "freq"}
                ],
            }
        )
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        _assert_code(resp, _ROLE_TYPE_MISMATCH)

    def test_truthful_role_still_uploads(self, client):
        payload = _bundle_payload(
            statmech={
                "external_symmetry": 1,
                "source_calculations": [
                    {"calculation_key": "opt0", "role": "opt"}
                ],
            }
        )
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# The conformer upload's applied energy corrections
# ---------------------------------------------------------------------------


def _zpe_correction(**overrides) -> dict:
    """One scale-factor correction, naming a declared calculation by default.

    The payload schema already requires a scale-factor correction to name
    the frequency calculation it was applied to, so the default here is a
    declared key; the tests vary it to exercise *resolution*.
    """
    base: dict = {
        "frequency_scale_factor": {
            "level_of_theory": _LOT,
            "scale_kind": "zpe",
            "value": 0.977,
        },
        "application_role": "zpe",
        "value": 0.0215,
        "value_unit": "hartree",
        "source_calculation_key": "my_sp_job",
    }
    base.update(overrides)
    return base


class TestConformerCorrectionSourceKeys:
    def test_undeclared_calculation_key_is_refused(self, client):
        payload = _conformer_payload(
            "conf-aec-ghost-calc",
            applied_energy_corrections=[
                _zpe_correction(source_calculation_key="my_sp_jbo")
            ],
        )
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        body = _assert_code(resp, _KEY_UNDECLARED)
        assert body["context"]["key"] == "my_sp_jbo"
        # The remedy names what *is* declared, so the fix is mechanical.
        assert body["context"]["declared_keys"] == ["my_sp_job"]

    def test_declared_calculation_key_is_accepted(self, client):
        payload = _conformer_payload(
            "conf-aec-good-calc",
            applied_energy_corrections=[
                _zpe_correction(source_calculation_key="my_sp_job")
            ],
        )
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text

    def test_conformer_key_that_is_not_the_label_is_refused(self, client):
        payload = _conformer_payload(
            "conf-aec-label",
            applied_energy_corrections=[
                _zpe_correction(source_conformer_key="some-other-conformer")
            ],
        )
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        body = _assert_code(resp, _KEY_UNDECLARED)
        assert body["context"]["declared_keys"] == ["conf-aec-label"]

    def test_conformer_key_matching_the_label_is_accepted(self, client):
        payload = _conformer_payload(
            "conf-aec-good-label",
            applied_energy_corrections=[
                _zpe_correction(source_conformer_key="conf-aec-good-label")
            ],
        )
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Thermo's copy of the rule, and the code it now carries
# ---------------------------------------------------------------------------


def _thermo_payload(smiles: str, **overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "h298_kj_mol": -83.7,
    }
    base.update(overrides)
    return base


class TestThermoRoleMismatchIsMachineReadable:
    """The same claim, refused by thermo, must name itself to a client.

    Statmech's refusal has carried a code since #152; thermo's raised a
    bare ``ValueError``, so the identical depositor mistake arrived as
    ``validation_error`` — a code no client can branch on and one that
    never appears in the generated ``RejectionCode`` constants. The two
    codes are deliberately different strings: same rule, different
    subject, and a client may want to tell a broken partition function
    from a broken enthalpy.
    """

    def test_freq_role_on_an_sp_calculation_is_refused_with_a_code(
        self, client
    ):
        payload = _thermo_payload(
            "CCCCO",
            calculations=[{"key": "my_sp_job", "calculation": _sp_calc()}],
            source_calculations=[
                {"calculation_key": "my_sp_job", "role": "freq"}
            ],
        )
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        body = _assert_code(resp, _THERMO_ROLE_TYPE_MISMATCH)
        assert body["context"]["declared_role"] == "freq"
        assert body["context"]["expected_calculation_type"] == "freq"
        assert body["context"]["actual_calculation_type"] == "sp"
        # The offending link is named by the depositor's own key, and no
        # row id is disclosed anywhere in the body (DR-0028 Req 2).
        assert "my_sp_job" in body["context"]["field"]
        assert "calculation_id" not in resp.text

    def test_the_code_is_not_statmechs(self, client):
        """Two subjects, two codes — a client must be able to tell them apart."""
        payload = _thermo_payload(
            "CCCCCO",
            calculations=[{"key": "my_sp_job", "calculation": _sp_calc()}],
            source_calculations=[
                {"calculation_key": "my_sp_job", "role": "freq"}
            ],
        )
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] != _ROLE_TYPE_MISMATCH

    def test_matching_roles_still_upload(self, client):
        payload = _thermo_payload(
            "CCCCCCO",
            calculations=[
                {"key": "my_sp_job", "calculation": _sp_calc()},
                {"key": "my_freq_job", "calculation": _freq_calc()},
            ],
            source_calculations=[
                {"calculation_key": "my_sp_job", "role": "sp"},
                {"calculation_key": "my_freq_job", "role": "freq"},
            ],
        )
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# The one exception: an ``opt`` may serve the ``sp`` role — one way only
# ---------------------------------------------------------------------------


class TestOptimisationMayServeTheSinglePointRole:
    """A depositor who ran no separate single point still has the energy.

    The optimisation's final energy *is* the single-point value, at the
    optimisation's own level of theory — which the calculation row it
    points at already carries, so nothing is left unstated. The
    mixed-method depositor is the other branch entirely: they have a real
    ``sp`` calculation and link the ``sp`` role to that.
    """

    def test_statmech_sp_role_accepts_an_opt_calculation(self, client):
        payload = _standalone_statmech_payload(
            species_entry={"smiles": "CCCCC", "charge": 0, "multiplicity": 1},
            calculations=[{"key": "my_opt_job", "calculation": _opt_calc()}],
            source_calculations=[
                {"calculation_key": "my_opt_job", "role": "sp"}
            ],
        )
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        assert resp.status_code == 201, resp.text

    def test_thermo_sp_role_accepts_an_opt_calculation(self, client):
        payload = _thermo_payload(
            "CCCCCC",
            calculations=[{"key": "my_opt_job", "calculation": _opt_calc()}],
            source_calculations=[
                {"calculation_key": "my_opt_job", "role": "sp"}
            ],
        )
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 201, resp.text

    def test_bundle_statmech_sp_role_accepts_an_opt_calculation(self, client):
        payload = _bundle_payload(
            statmech={
                "external_symmetry": 1,
                "source_calculations": [
                    {"calculation_key": "opt0", "role": "sp"}
                ],
            }
        )
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 201, resp.text

    def test_bundle_thermo_sp_role_accepts_an_opt_calculation(self, client):
        payload = _bundle_payload(
            thermo={
                "h298_kj_mol": 217.998,
                "source_calculations": [
                    {"calculation_key": "opt0", "role": "sp"}
                ],
            }
        )
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 201, resp.text


class TestTheExceptionDoesNotRunInReverse:
    """An ``sp`` calculation optimised nothing, so it cannot be the ``opt``.

    This asymmetry is the part a later reader is most likely to "tidy up".
    It is not an oversight: ``role='opt'`` is the claim that the linked job
    produced the converged geometry the record stands on, and a
    single-point job produced no geometry at all. The accepted-type list in
    the refusal's ``context`` is asserted here so the one-sidedness is
    visible in the response, not only in the source.
    """

    def test_statmech_opt_role_refuses_an_sp_calculation(self, client):
        payload = _standalone_statmech_payload(
            species_entry={"smiles": "CCCCCC", "charge": 0, "multiplicity": 1},
            calculations=[{"key": "my_sp_job", "calculation": _sp_calc()}],
            source_calculations=[
                {"calculation_key": "my_sp_job", "role": "opt"}
            ],
        )
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        body = _assert_code(resp, _ROLE_TYPE_MISMATCH)
        assert body["context"]["declared_role"] == "opt"
        assert body["context"]["actual_calculation_type"] == "sp"
        assert body["context"]["accepted_calculation_types"] == ["opt"]

    def test_thermo_opt_role_refuses_an_sp_calculation(self, client):
        payload = _thermo_payload(
            "CCCCCCC",
            calculations=[{"key": "my_sp_job", "calculation": _sp_calc()}],
            source_calculations=[
                {"calculation_key": "my_sp_job", "role": "opt"}
            ],
        )
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        body = _assert_code(resp, _THERMO_ROLE_TYPE_MISMATCH)
        assert body["context"]["declared_role"] == "opt"
        assert body["context"]["actual_calculation_type"] == "sp"
        assert body["context"]["accepted_calculation_types"] == ["opt"]

    def test_sp_role_still_refuses_a_freq_calculation(self, client):
        """The exception is one named type, not "anything with an energy"."""
        payload = _standalone_statmech_payload(
            species_entry={"smiles": "CCCCCCC", "charge": 0, "multiplicity": 1},
            calculations=[{"key": "my_freq_job", "calculation": _freq_calc()}],
            source_calculations=[
                {"calculation_key": "my_freq_job", "role": "sp"}
            ],
        )
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        body = _assert_code(resp, _ROLE_TYPE_MISMATCH)
        assert body["context"]["accepted_calculation_types"] == ["sp", "opt"]

    def test_thermo_sp_role_still_refuses_a_freq_calculation(self, client):
        payload = _thermo_payload(
            "CCCCCCCC",
            calculations=[{"key": "my_freq_job", "calculation": _freq_calc()}],
            source_calculations=[
                {"calculation_key": "my_freq_job", "role": "sp"}
            ],
        )
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        body = _assert_code(resp, _THERMO_ROLE_TYPE_MISMATCH)
        assert body["context"]["accepted_calculation_types"] == ["sp", "opt"]
