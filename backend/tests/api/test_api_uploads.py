"""Tests for the upload API endpoints."""

from __future__ import annotations


def _hydrogen_conformer_payload(label: str = "conf-a") -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": label,
        "note": "test upload",
    }


_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


def _freq_calc(
    *,
    n_imag: int,
    imag_freq_cm1: float | None = None,
    frequencies: list[float] | None = None,
    reaction_coordinate_mode_index: int | None = None,
    dispositions: dict[int, str] | None = None,
    parameters: list[dict] | None = None,
) -> dict:
    result: dict = {"n_imag": n_imag, "imag_freq_cm1": imag_freq_cm1}
    if frequencies is not None:
        dispositions = dispositions or {}
        result["modes"] = [
            {
                "mode_index": index + 1,
                "frequency_cm1": value,
                "is_imaginary": value < 0,
                "imaginary_disposition": dispositions.get(index + 1),
            }
            for index, value in enumerate(frequencies)
        ]
    if reaction_coordinate_mode_index is not None:
        result["reaction_coordinate_mode_index"] = reaction_coordinate_mode_index
    calc: dict = {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "freq_result": result,
    }
    if parameters is not None:
        calc["parameters"] = parameters
    return calc


def _transition_state_payload(
    *,
    n_imag: int | None = 1,
    imag_freq_cm1: float | None = -1500.0,
    label: str = "ts-a",
    **freq_kwargs,
) -> dict:
    payload: dict = {
        "reaction": {
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1}},
            ],
            "products": [
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
                {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
            ],
        },
        "charge": 0,
        "multiplicity": 2,
        "geometry": {
            # H + CH4 -> H2 + CH3, so the saddle point is CH5. This block was
            # a three-atom stub (H, C, H) until
            # ``validate_transition_state_composition`` started refusing a
            # saddle point that is not made of its own reaction's atoms; the
            # coordinates stay schematic, the composition does not.
            "xyz_text": (
                "6\nH...H-CH3 abstraction TS\n"
                "C  0.000  0.000  0.000\n"
                "H -0.510  0.883  0.000\n"
                "H -0.510 -0.883  0.000\n"
                "H  0.000  0.000 -1.090\n"
                "H  0.000  0.000  1.350\n"
                "H  0.000  0.000  2.250"
            ),
        },
        "primary_opt": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "additional_calculations": [],
        "label": label,
    }
    if n_imag is not None:
        payload["additional_calculations"] = [
            _freq_calc(
                n_imag=n_imag, imag_freq_cm1=imag_freq_cm1, **freq_kwargs
            )
        ]
    return payload


#: ADR 0012's motivating record, in the shape a producer deposits it: the
#: complete signed frequency list, the designated reaction coordinate, and
#: a declared disposition for each other imaginary mode.
_MOTIVATING_FREQUENCIES = [-1300.0, -42.0, -13.0, 800.0, 1400.0]
_MOTIVATING_DISPOSITIONS = {2: "torsion", 3: "rigid_body_residue"}


def _motivating_transition_state_payload(
    *, label: str, parameters: list[dict] | None = None
) -> dict:
    return _transition_state_payload(
        n_imag=3,
        imag_freq_cm1=-1300.0,
        label=label,
        frequencies=_MOTIVATING_FREQUENCIES,
        reaction_coordinate_mode_index=1,
        dispositions=_MOTIVATING_DISPOSITIONS,
        parameters=parameters,
    )


def _reaction_payload() -> dict:
    return {
        "reversible": True,
        "reactants": [
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        ],
        "products": [
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        ],
    }


# ---------------------------------------------------------------------------
# Conformer upload
# ---------------------------------------------------------------------------


class TestConformerUpload:
    def test_success(self, client):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "conformer_observation"
        assert "id" in data
        assert "species_entry_id" in data
        assert "conformer_group_id" in data

    def test_invalid_smiles_returns_422(self, client):
        payload = _hydrogen_conformer_payload()
        payload["species_entry"]["smiles"] = "NOT_A_SMILES"
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422

    def test_missing_geometry_returns_422(self, client):
        payload = _hydrogen_conformer_payload()
        del payload["geometry"]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Imaginary-frequency validation tiers (ADR 0008)
#
# minimum + n_imag >= 1     -> block   (definition)
# vdw_complex + n_imag >= 1 -> warn    (expectation: Hessian grid noise)
# TS + n_imag == 0          -> block   (no reaction coordinate)
# TS + extras undesignated  -> block   (contract, ADR 0012)
# TS + extras declared      -> warn    (judged against tau, ADR 0012)
# TS + |imag| < threshold   -> warn    (expectation: flat/variational)
# ---------------------------------------------------------------------------


class TestMinimumImaginaryModeBlocks:
    def test_minimum_with_one_imaginary_mode_returns_422(self, client):
        payload = _hydrogen_conformer_payload(label="conf-imag")
        payload["additional_calculations"] = [_freq_calc(n_imag=1)]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422
        assert "n_imag_contradicts_minimum" in resp.text

    def test_minimum_with_higher_order_saddle_returns_422(self, client):
        payload = _hydrogen_conformer_payload(label="conf-saddle")
        payload["additional_calculations"] = [_freq_calc(n_imag=2)]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422
        assert "n_imag_higher_order_saddle" in resp.text

    def test_minimum_blocking_ignores_magnitude(self, client):
        """ADR 0008 forbids a magnitude threshold on a blocking check, so a
        tiny imaginary mode on a declared minimum is refused just the same."""
        payload = _hydrogen_conformer_payload(label="conf-tiny-imag")
        payload["additional_calculations"] = [
            _freq_calc(n_imag=1, imag_freq_cm1=-3.0)
        ]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422

    def test_zero_imaginary_modes_still_uploads(self, client):
        payload = _hydrogen_conformer_payload(label="conf-real-min")
        payload["additional_calculations"] = [_freq_calc(n_imag=0)]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text
        assert resp.json()["warnings"] == []

    def test_no_frequency_evidence_is_unaffected(self, client):
        """Absence is not contradiction — the base payload has no freq data."""
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(label="conf-no-freq"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["warnings"] == []


class TestVdwComplexImaginaryModeWarns:
    def test_vdw_complex_with_one_imaginary_mode_is_accepted(self, client):
        payload = _hydrogen_conformer_payload(label="vdw-imag")
        payload["species_entry"]["species_entry_kind"] = "vdw_complex"
        payload["additional_calculations"] = [
            _freq_calc(n_imag=1, imag_freq_cm1=-22.0)
        ]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text
        codes = {w["code"] for w in resp.json()["warnings"]}
        assert "n_imag_contradicts_minimum" in codes
        assert "n_imag_suggests_transition_state" not in codes

    def test_vdw_complex_with_higher_order_saddle_is_accepted(self, client):
        payload = _hydrogen_conformer_payload(label="vdw-saddle")
        payload["species_entry"]["species_entry_kind"] = "vdw_complex"
        payload["additional_calculations"] = [_freq_calc(n_imag=3)]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text
        codes = {w["code"] for w in resp.json()["warnings"]}
        assert "n_imag_higher_order_saddle" in codes

    def test_the_same_saddle_contradicted_by_its_own_modes_is_refused(
        self, client
    ):
        """The matched partner of the test above, and the whole asymmetry.

        Identical payload, identical ``n_imag = 3`` — the only change is
        that a frequency list is deposited beside the scalar and shows
        one imaginary mode rather than three. The test above is accepted
        because absence is incompleteness; this one is refused because
        the record now answers the same question two ways, and no re-run
        can decide which answer was meant.

        Written as a pair on purpose. A rule phrased "mode rows must
        exist" would refuse both, which would mean refusing a deposit
        TCKDB has always taken and still means to.
        """
        payload = _hydrogen_conformer_payload(label="vdw-contradicted")
        payload["species_entry"]["species_entry_kind"] = "vdw_complex"
        payload["additional_calculations"] = [
            _freq_calc(n_imag=3, frequencies=[-22.0, 900.0, 1600.0])
        ]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "freq_n_imag_disagrees_with_modes"

    def test_a_frequency_list_that_agrees_is_accepted_with_the_same_warning(
        self, client
    ):
        """Three imaginary modes, deposited and agreeing, still upload.

        The guard against the check above having quietly become a ban on
        higher-order saddles: the payload that made the point of
        ``test_vdw_complex_with_higher_order_saddle_is_accepted`` is
        accepted with its list attached too, and warns for the same
        reason it always did.

        **The geometry is widened to two atoms here, and it has to be.**
        Every other test in this class runs on the one-hydrogen-atom
        fixture, which was fine while nothing read the geometry — but a
        single atom has ``3N = 3`` degrees of freedom in total and this
        test deposits *four* frequencies, a combination no harmonic
        analysis produces and
        ``freq_list_exceeds_geometry_degrees_of_freedom`` now refuses.
        Two atoms have six, which hold the list. The same fixture defect
        was found and fixed once already, on ``_TS_XYZ`` in
        ``tests/schemas/test_stationary_point_validation_tiers.py``; this
        copy survived because the bound was reported as a warning nobody
        asserted on, and the promotion to a refusal is what surfaced it.
        The property under test is untouched: three declared imaginary
        modes, agreeing with ``n_imag``, on a kind for which several
        imaginary modes are an expectation.
        """
        payload = _hydrogen_conformer_payload(label="vdw-saddle-listed")
        payload["species_entry"]["smiles"] = "[H][H]"
        payload["species_entry"]["multiplicity"] = 1
        payload["species_entry"]["species_entry_kind"] = "vdw_complex"
        payload["geometry"]["xyz_text"] = (
            "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.741"
        )
        payload["additional_calculations"] = [
            _freq_calc(n_imag=3, frequencies=[-22.0, -14.0, -9.0, 1600.0])
        ]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text
        codes = {w["code"] for w in resp.json()["warnings"]}
        assert "n_imag_higher_order_saddle" in codes

    def test_stiff_vdw_mode_also_suggests_a_transition_state(self, client):
        payload = _hydrogen_conformer_payload(label="vdw-stiff")
        payload["species_entry"]["species_entry_kind"] = "vdw_complex"
        payload["additional_calculations"] = [
            _freq_calc(n_imag=1, imag_freq_cm1=-1200.0)
        ]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text
        codes = {w["code"] for w in resp.json()["warnings"]}
        assert "n_imag_suggests_transition_state" in codes


class TestTransitionStateImaginaryModeTiers:
    def test_one_imaginary_mode_uploads_cleanly(self, client):
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(label="ts-ok"),
        )
        assert resp.status_code == 201, resp.text
        codes = {w["code"] for w in resp.json()["warnings"]}
        assert "transition_state_imaginary_frequency_too_small" not in codes

    def test_zero_imaginary_modes_returns_422(self, client):
        """Unchanged in tier by ADR 0012, renamed in code: no imaginary
        mode means no reaction coordinate at all."""
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                n_imag=0, imag_freq_cm1=None, label="ts-min"
            ),
        )
        assert resp.status_code == 422
        assert "transition_state_no_imaginary_mode" in resp.text

    def test_extra_modes_without_a_designation_return_422(self, client):
        """Was ``test_two_imaginary_modes_returns_422``, which asserted the
        retired count-based rule. The refusal survives, but on the contract:
        the payload will not say which mode is the reaction coordinate."""
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                n_imag=2, imag_freq_cm1=-900.0, label="ts-saddle"
            ),
        )
        assert resp.status_code == 422
        assert "transition_state_reaction_coordinate_not_designated" in resp.text

    def test_the_motivating_record_uploads_with_a_warning(self, client):
        """-1300 / -42 / -13 cm⁻¹ over HTTP. Refused outright before ADR
        0012; accepted and annotated now."""
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_motivating_transition_state_payload(label="ts-adr0012"),
        )
        assert resp.status_code == 201, resp.text
        codes = {w["code"] for w in resp.json()["warnings"]}
        assert "transition_state_extra_imaginary_modes_below_tau" in codes
        assert "transition_state_reaction_coordinate_not_designated" not in codes

    def test_a_tight_recorded_protocol_flags_the_same_record(self, client):
        """The same three frequencies, deposited with provenance saying the
        Hessian was analytic on a tight grid. -42 cm⁻¹ is then real negative
        curvature rather than quadrature noise, so the record is flagged —
        accepted either way, which is what makes τ safe to read from
        provenance a payload may not carry."""
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_motivating_transition_state_payload(
                label="ts-adr0012-tight",
                parameters=[
                    {
                        "raw_key": "anfreq",
                        "raw_value": "true",
                        "canonical_key": "freq.hessian_method",
                        "canonical_value": "analytic",
                        "section": "freq",
                    },
                    {
                        "raw_key": "grid",
                        "raw_value": "ultrafine",
                        "canonical_key": "grid.quality",
                        "canonical_value": "ultrafine",
                        "section": "integral",
                    },
                    {
                        "raw_key": "tight",
                        "raw_value": "true",
                        "canonical_key": "opt.convergence",
                        "canonical_value": "tight",
                        "section": "opt",
                    },
                ],
            ),
        )
        assert resp.status_code == 201, resp.text
        codes = {w["code"] for w in resp.json()["warnings"]}
        assert "transition_state_extra_imaginary_mode_above_tau" in codes

    def test_an_undeclared_mode_stiffer_than_the_barrier_returns_422(self, client):
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                n_imag=2,
                imag_freq_cm1=-400.0,
                label="ts-ambiguous",
                frequencies=[-400.0, -900.0, 700.0],
                reaction_coordinate_mode_index=1,
            ),
        )
        assert resp.status_code == 422
        assert "transition_state_reaction_coordinate_ambiguous" in resp.text

    def test_small_imaginary_mode_is_accepted_with_a_warning(self, client):
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                n_imag=1, imag_freq_cm1=-30.0, label="ts-soft"
            ),
        )
        assert resp.status_code == 201, resp.text
        codes = {w["code"] for w in resp.json()["warnings"]}
        assert "transition_state_imaginary_frequency_too_small" in codes

    def test_no_frequency_evidence_is_unaffected(self, client):
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(n_imag=None, label="ts-no-freq"),
        )
        assert resp.status_code == 201, resp.text
        codes = {w["code"] for w in resp.json()["warnings"]}
        assert "transition_state_no_imaginary_mode" not in codes
        assert "transition_state_reaction_coordinate_not_designated" not in codes
        assert "transition_state_imaginary_frequency_too_small" not in codes


# ---------------------------------------------------------------------------
# Reaction upload
# ---------------------------------------------------------------------------


class TestReactionUpload:
    def test_success(self, client):
        resp = client.post(
            "/api/v1/uploads/reactions",
            json=_reaction_payload(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "reaction_entry"
        assert "id" in data
        assert "reaction_id" in data

    def test_missing_reactants_returns_422(self, client):
        payload = _reaction_payload()
        payload["reactants"] = []
        resp = client.post("/api/v1/uploads/reactions", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Thermo upload
# ---------------------------------------------------------------------------


class TestThermoUpload:
    def test_success(self, client):
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "thermo"
        assert "species_entry_id" in data

    def _seed_calc(self, client, *, calc_type: str = "sp") -> tuple[int, int]:
        """Insert a Calculation directly into the test session and return
        (species_entry_id, calculation_id). Mirrors the conformer-upload
        pre-state that DR-0028's existing_calculation_id path consumes."""
        from app.db.models.calculation import Calculation
        from app.db.models.common import CalculationType
        from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
        from app.services.species_resolution import resolve_species_entry

        session = client._db_session
        entry = resolve_species_entry(
            session,
            SpeciesEntryIdentityPayload(
                smiles="CC", charge=0, multiplicity=1,
            ),
        )
        calc = Calculation(
            type=CalculationType(calc_type),
            species_entry_id=entry.id,
        )
        session.add(calc)
        session.flush()
        return entry.id, calc.id

    def test_existing_calculation_id_links_thermo_to_existing_row(self, client):
        """DR-0028: existing_calculation_id wired through the API produces a
        201, with the thermo attached to the same species entry."""
        species_entry_id, calc_id = self._seed_calc(client, calc_type="sp")
        payload = {
            "species_entry": {"smiles": "CC", "charge": 0, "multiplicity": 1},
            "scientific_origin": "computed",
            "h298_kj_mol": -83.7,
            "source_calculations": [
                {"existing_calculation_id": calc_id, "role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["species_entry_id"] == species_entry_id

    def test_existing_calculation_id_not_found_returns_404(self, client):
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {"existing_calculation_id": 999_999_999, "role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 404, resp.text
        assert "does not exist" in resp.json()["detail"]

    def test_existing_calc_wrong_species_entry_returns_422(self, client):
        """A cross-owner chained citation is refused, under a real code.

        This is the response #161 was about. The refusal used to be a bare
        ``ValueError`` phrased ``f"{context}: …"`` where ``context`` ended
        in ``existing_calculation_id``, and the envelope scraped that field
        name out of the sentence and advertised it as ``code``. A client
        could branch on a string that was never a contract and that moved
        with any rewording. The code is now declared by the refusal.
        """
        # Seed a calc owned by a CC species, then upload thermo for a
        # different species (H) that references it.
        _, calc_id = self._seed_calc(client, calc_type="sp")
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {"existing_calculation_id": calc_id, "role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "thermo_source_calculation_owner_mismatch", body
        # The fabricated code must never come back: a field name is not a
        # code, and no register or generated client constant holds it.
        assert body["code"] != "existing_calculation_id", body
        detail = body["detail"]
        assert "another species entry" in detail
        # The field the depositor wrote is still named, in context.
        assert body["context"]["field"].endswith("existing_calculation_id")
        # No internal id values leaked.
        assert str(calc_id) not in detail
        assert "species_entry_id=" not in detail

    def test_existing_calc_role_type_mismatch_returns_422(self, client):
        _, freq_calc_id = self._seed_calc(client, calc_type="freq")
        payload = {
            "species_entry": {"smiles": "CC", "charge": 0, "multiplicity": 1},
            "scientific_origin": "computed",
            "h298_kj_mol": -83.7,
            "source_calculations": [
                # Role=sp pointing at a freq calc
                {"existing_calculation_id": freq_calc_id, "role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422, resp.text
        assert "incompatible" in resp.json()["detail"]

    def test_both_reference_fields_set_returns_422(self, client):
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {
                    "calculation_key": "ghost",
                    "existing_calculation_id": 1,
                    "role": "sp",
                }
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422

    def test_neither_reference_field_set_returns_422(self, client):
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {"role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency and deduplication
# ---------------------------------------------------------------------------


class TestUploadIdempotency:
    def test_duplicate_conformer_creates_separate_observations(self, client):
        """Two identical conformer uploads should create two observations
        but share the same species and species entry."""
        r1 = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(label="obs-1"),
        )
        r2 = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(label="obs-2"),
        )
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        # Different observation rows
        assert d1["id"] != d2["id"]
        # Same species entry (deduplication)
        assert d1["species_entry_id"] == d2["species_entry_id"]

    def test_duplicate_reaction_creates_new_entry_same_graph(self, client):
        """Two identical reaction uploads should share the graph-level
        chem_reaction but create separate reaction entries."""
        r1 = client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        r2 = client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        # Different entries
        assert d1["id"] != d2["id"]
        # Same graph-level reaction (deduplication)
        assert d1["reaction_id"] == d2["reaction_id"]

    def test_repeated_thermo_creates_separate_records(self, client):
        """Two identical thermo uploads should create separate thermo records
        for the same species entry."""
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
        }
        r1 = client.post("/api/v1/uploads/thermo", json=payload)
        r2 = client.post("/api/v1/uploads/thermo", json=payload)
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        assert d1["id"] != d2["id"]
        assert d1["species_entry_id"] == d2["species_entry_id"]


# ---------------------------------------------------------------------------
# Read-after-write round trip
# ---------------------------------------------------------------------------


class TestReadAfterWrite:
    def test_conformer_upload_then_read(self, client):
        """Upload a conformer, then GET the species entry and verify the
        conformer appears in the nested list."""
        upload = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(),
        ).json()
        entry_id = upload["species_entry_id"]

        conformers = client.get(
            f"/api/v1/species-entries/{entry_id}/conformers"
        ).json()
        assert any(c["id"] == upload["id"] for c in conformers)

    def test_reaction_upload_then_read(self, client):
        """Upload a reaction, then GET the reaction entry by ID."""
        upload = client.post(
            "/api/v1/uploads/reactions", json=_reaction_payload()
        ).json()
        entry = client.get(
            f"/api/v1/reaction-entries/{upload['id']}"
        )
        assert entry.status_code == 200
        assert entry.json()["id"] == upload["id"]

    def test_upload_with_explicit_input_geometry_for_opt_round_trip(
        self, client
    ):
        """A producer-explicit ``input_geometries`` for an opt calc must
        round-trip through ``GET /api/v1/calculations/{opt_id}/input-geometries``
        with the declared xyz preserved in the resolved geometry row."""
        pre_opt_xyz = "2\npre-opt H2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.81"
        payload = {
            "species_entry": {
                "smiles": "[H][H]",
                "charge": 0,
                "multiplicity": 1,
            },
            "geometry": {
                "xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74",
            },
            "calculation": {
                "type": "opt",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "opt_result": {"converged": True},
                "input_geometries": [{"xyz_text": pre_opt_xyz}],
            },
            "label": "h2-explicit-opt-input",
        }
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text
        opt_id = resp.json()["primary_calculation"]["calculation_id"]

        listed = client.get(
            f"/api/v1/calculations/{opt_id}/input-geometries"
        )
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["input_order"] == 1
        # Two atoms came in; the resolved Geometry must reflect that.
        assert rows[0]["geometry"]["natoms"] == 2

    def test_conformer_upload_freq_sp_have_input_geometries(self, client):
        """Conformer upload with primary opt + freq + sp additionals must
        produce one row in calculation_input_geometry for each of the
        freq/sp calcs (visible via GET .../input-geometries) and zero
        rows for the primary opt calc.
        """
        payload = {
            "species_entry": {
                "smiles": "[H][H]",
                "charge": 0,
                "multiplicity": 1,
            },
            "geometry": {
                "xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74",
            },
            "calculation": {
                "type": "opt",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "opt_result": {"converged": True},
            },
            "additional_calculations": [
                {
                    "type": "freq",
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "level_of_theory": {
                        "method": "B3LYP", "basis": "6-31G(d)"
                    },
                    "freq_result": {"n_imag": 0, "zpe_hartree": 0.010},
                },
                {
                    "type": "sp",
                    "software_release": {"name": "Orca", "version": "5.0"},
                    "level_of_theory": {
                        "method": "CCSD(T)", "basis": "cc-pVTZ"
                    },
                    "sp_result": {"electronic_energy_hartree": -1.195},
                },
            ],
            "label": "h2-input-geom",
        }
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201
        body = resp.json()

        opt_id = body["primary_calculation"]["calculation_id"]
        additionals = {
            ref["type"]: ref["calculation_id"]
            for ref in body["additional_calculations"]
        }
        freq_id = additionals["freq"]
        sp_id = additionals["sp"]

        opt_inputs = client.get(
            f"/api/v1/calculations/{opt_id}/input-geometries"
        ).json()
        assert opt_inputs == []

        freq_inputs = client.get(
            f"/api/v1/calculations/{freq_id}/input-geometries"
        ).json()
        assert len(freq_inputs) == 1

        sp_inputs = client.get(
            f"/api/v1/calculations/{sp_id}/input-geometries"
        ).json()
        assert len(sp_inputs) == 1

    def test_upload_with_explicit_scan_output_geometries_round_trip(
        self, client
    ):
        """Bundle upload with a scan additional calc declaring three
        scan-point output geometries must round-trip through
        ``GET /api/v1/calculations/{scan_id}/output-geometries`` with
        the declared roles preserved and ordered by ``output_order``."""
        xyz_a = "1\nscan-1\nH 0.0 0.0 0.10"
        xyz_b = "1\nscan-2\nH 0.0 0.0 0.20"
        xyz_c = "1\nscan-3\nH 0.0 0.0 0.30"
        payload = {
            "species_entry": {
                "smiles": "[H]",
                "charge": 0,
                "multiplicity": 2,
            },
            "conformers": [
                {
                    "key": "c0",
                    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
                    "primary_calculation": {
                        "key": "opt0",
                        "type": "opt",
                        "software_release": {
                            "name": "Gaussian", "version": "16"
                        },
                        "level_of_theory": {
                            "method": "B3LYP", "basis": "6-31G(d)"
                        },
                        "opt_result": {"converged": True},
                    },
                    "additional_calculations": [
                        {
                            "key": "scan0",
                            "type": "scan",
                            "software_release": {
                                "name": "Gaussian", "version": "16"
                            },
                            "level_of_theory": {
                                "method": "B3LYP", "basis": "6-31G(d)"
                            },
                            "output_geometries": [
                                {
                                    "geometry": {"xyz_text": xyz_a},
                                    "role": "scan_point",
                                },
                                {
                                    "geometry": {"xyz_text": xyz_b},
                                    "role": "scan_point",
                                },
                                {
                                    "geometry": {"xyz_text": xyz_c},
                                    "role": "scan_point",
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        resp = client.post(
            "/api/v1/uploads/computed-species", json=payload
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        scan_id = next(
            ref["calculation_id"]
            for ref in body["conformers"][0]["additional_calculations"]
            if ref["key"] == "scan0"
        )

        listed = client.get(
            f"/api/v1/calculations/{scan_id}/output-geometries"
        )
        assert listed.status_code == 200
        rows = listed.json()
        assert [r["output_order"] for r in rows] == [1, 2, 3]
        assert [r["role"] for r in rows] == [
            "scan_point", "scan_point", "scan_point"
        ]
        assert len({r["geometry"]["id"] for r in rows}) == 3
