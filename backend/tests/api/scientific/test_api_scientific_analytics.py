"""The bounded analytics surface: /api/v1/scientific/analytics/*.

Four endpoints exist so quantitative dataset construction has one indexed,
documented place to happen. These tests cover what makes that claim true:
the filters select what they say they select, the read profile is honoured
*and* echoed, review visibility gates the result set, keyset traversal walks
a stable sequence, and every malformed request is a coded 422 rather than a
plausible-looking empty page.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    CalculationType,
    KineticsDirection,
    KineticsModelKind,
    PhaseKind,
    PressureContext,
    RecordReviewStatus,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechTreatmentKind,
    SubmissionRecordType,
    ThermoModelKind,
    TunnelingModel,
)
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_opt_result,
    attach_sp_result,
    attach_spin_diagnostic,
    attach_statmech_electronic_level,
    attach_statmech_torsion,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
    unique_smiles,
)

KINETICS = "/api/v1/scientific/analytics/kinetics"
THERMO = "/api/v1/scientific/analytics/thermo"
STATMECH = "/api/v1/scientific/analytics/statmech"
CALCULATIONS = "/api/v1/scientific/analytics/calculations"

ALL_ENDPOINTS = (KINETICS, THERMO, STATMECH, CALCULATIONS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _entry(session):
    species = make_species(
        session, smiles=unique_smiles(), inchi_key=next_inchi_key("ANA")
    )
    return make_species_entry(session, species)


@pytest.fixture
def kinetics_corpus(db_session):
    """Three kinetics records spanning every kinetics filter axis."""
    reactant = _entry(db_session)
    product = _entry(db_session)
    reaction = make_chem_reaction(
        db_session,
        reactants=[reactant.species],
        products=[product.species],
    )
    entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[reactant],
        product_entries=[product],
    )

    low = make_kinetics(
        db_session,
        reaction_entry=entry,
        a=1.0e-12,
        n=1.0,
        ea_kj_mol=10.0,
        tmin_k=300.0,
        tmax_k=2000.0,
        direction=KineticsDirection.forward,
        tunneling_model=TunnelingModel.eckart,
        pressure_context=PressureContext.apparent_at_pressure,
        pressure_bar=1.0,
        degeneracy=2.0,
        ea_uncertainty_kj_mol=2.0,
    )
    high = make_kinetics(
        db_session,
        reaction_entry=entry,
        a=5.0e-11,
        n=2.5,
        ea_kj_mol=90.0,
        tmin_k=800.0,
        tmax_k=1200.0,
        direction=KineticsDirection.reverse,
        tunneling_model=TunnelingModel.wigner,
        pressure_context=PressureContext.high_p_limit,
        degeneracy=6.0,
    )
    experimental = make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
        model_kind=KineticsModelKind.arrhenius,
        a=3.0e-12,
        n=0.0,
        ea_kj_mol=50.0,
        tmin_k=None,
        tmax_k=None,
    )
    db_session.flush()
    return {"low": low, "high": high, "experimental": experimental}


@pytest.fixture
def thermo_corpus(db_session):
    """Two thermo records that differ on every thermo filter axis."""
    entry = _entry(db_session)
    cold = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-250.0, s298_j_mol_k=180.0
    )
    cold.phase = PhaseKind.gas
    cold.model_kind = ThermoModelKind.scalar
    cold.reference_pressure_bar = 1.0
    cold.enthalpy_formation_0k_kj_mol = -240.0
    cold.h298_uncertainty_kj_mol = 1.5

    hot = make_thermo_scalar(
        db_session,
        species_entry=entry,
        h298_kj_mol=120.0,
        s298_j_mol_k=320.0,
        scientific_origin=ScientificOriginKind.experimental,
    )
    hot.phase = PhaseKind.liquid
    hot.model_kind = ThermoModelKind.nasa7
    hot.reference_pressure_bar = 1.01325
    db_session.flush()
    return {"cold": cold, "hot": hot}


@pytest.fixture
def statmech_corpus(db_session):
    """Two statmech records differing on symmetry, rotor and child rows."""
    entry = _entry(db_session)
    asym = make_statmech(
        db_session,
        species_entry=entry,
        external_symmetry=2,
        point_group="C2v",
        is_linear=False,
        statmech_treatment=StatmechTreatmentKind.rrho,
        optical_isomers=1,
    )
    asym.rigid_rotor_kind = RigidRotorKind.asymmetric_top
    asym.rotational_constant_a_cm1 = 9.5
    asym.rotational_constant_b_cm1 = 1.2
    asym.rotational_constant_c_cm1 = 0.9
    db_session.flush()
    attach_statmech_torsion(db_session, statmech=asym, torsion_index=1)
    attach_statmech_electronic_level(
        db_session, statmech=asym, level_index=1, energy_cm1=0.0, degeneracy=1
    )
    attach_statmech_electronic_level(
        db_session, statmech=asym, level_index=2, energy_cm1=140.0, degeneracy=2
    )

    linear = make_statmech(
        db_session,
        species_entry=entry,
        external_symmetry=1,
        point_group="Cinfv",
        is_linear=True,
        statmech_treatment=StatmechTreatmentKind.rrho_1d,
        optical_isomers=2,
    )
    linear.rigid_rotor_kind = RigidRotorKind.linear
    linear.rotational_constant_a_cm1 = 0.4
    db_session.flush()
    return {"asym": asym, "linear": linear}


@pytest.fixture
def calculation_corpus(db_session):
    """One SP, one converged opt, one freq with an imaginary mode."""
    entry = _entry(db_session)
    lot = make_lot(db_session, method="ccsdt", basis="ccpvtz")

    sp = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    attach_sp_result(db_session, calculation=sp, electronic_energy_hartree=-76.42)
    attach_spin_diagnostic(db_session, calculation=sp, s_squared=0.7538)

    opt = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    attach_opt_result(
        db_session, calculation=opt, final_energy_hartree=-76.40, converged=True
    )

    freq = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    attach_freq_result(
        db_session,
        calculation=freq,
        frequencies_cm1=[-1200.0, 500.0, 1500.0],
        zpe_hartree=0.021,
    )
    db_session.flush()
    return {"sp": sp, "opt": opt, "freq": freq, "lot": lot}


def _refs(body, key):
    return [record[key] for record in body["records"]]


# ---------------------------------------------------------------------------
# Kinetics filters
# ---------------------------------------------------------------------------


def test_kinetics_arrhenius_ranges_select_the_right_records(client, kinetics_corpus):
    body = client.get(f"{KINETICS}?ea_min_kj_mol=5&ea_max_kj_mol=20").json()
    assert _refs(body, "kinetics_ref") == [kinetics_corpus["low"].public_ref]

    body = client.get(f"{KINETICS}?n_min=2.0").json()
    assert _refs(body, "kinetics_ref") == [kinetics_corpus["high"].public_ref]

    body = client.get(f"{KINETICS}?a_min=1e-11").json()
    assert _refs(body, "kinetics_ref") == [kinetics_corpus["high"].public_ref]


def test_kinetics_enum_filters(client, kinetics_corpus):
    for query, expected in (
        ("direction=forward", "low"),
        ("direction=reverse", "high"),
        ("tunneling_model=eckart", "low"),
        ("pressure_context=high_p_limit", "high"),
        ("scientific_origin=experimental", "experimental"),
        ("model_kind=arrhenius", "experimental"),
    ):
        body = client.get(f"{KINETICS}?{query}").json()
        assert _refs(body, "kinetics_ref") == [
            kinetics_corpus[expected].public_ref
        ], query


def test_kinetics_degeneracy_and_pressure_ranges(client, kinetics_corpus):
    body = client.get(f"{KINETICS}?degeneracy_min=5").json()
    assert _refs(body, "kinetics_ref") == [kinetics_corpus["high"].public_ref]

    body = client.get(f"{KINETICS}?pressure_min_bar=0.5&pressure_max_bar=2").json()
    assert _refs(body, "kinetics_ref") == [kinetics_corpus["low"].public_ref]


def test_kinetics_uncertainty_presence_and_range(client, kinetics_corpus):
    body = client.get(f"{KINETICS}?has_uncertainty=true").json()
    assert _refs(body, "kinetics_ref") == [kinetics_corpus["low"].public_ref]

    body = client.get(f"{KINETICS}?ea_uncertainty_max_kj_mol=1").json()
    assert body["records"] == []

    body = client.get(f"{KINETICS}?has_uncertainty=false").json()
    assert kinetics_corpus["low"].public_ref not in _refs(body, "kinetics_ref")


def test_kinetics_temperature_filter_is_coverage_not_overlap(
    client, kinetics_corpus
):
    """A record with no stated T range must not match: unstated != unbounded."""
    body = client.get(f"{KINETICS}?temperature_min_k=900&temperature_max_k=1100").json()
    refs = set(_refs(body, "kinetics_ref"))
    assert refs == {
        kinetics_corpus["low"].public_ref,
        kinetics_corpus["high"].public_ref,
    }
    assert kinetics_corpus["experimental"].public_ref not in refs

    # 2500 K is outside every record's stated window.
    body = client.get(f"{KINETICS}?temperature_max_k=2500").json()
    assert body["records"] == []


def test_kinetics_provenance_presence_filters(client, kinetics_corpus):
    body = client.get(f"{KINETICS}?has_literature=true").json()
    assert body["records"] == []

    body = client.get(f"{KINETICS}?has_statmech_provenance=false").json()
    assert len(body["records"]) == 3
    assert all(
        record["has_statmech_provenance"] is False for record in body["records"]
    )

    body = client.get(f"{KINETICS}?has_transition_state_provenance=true").json()
    assert body["records"] == []

    body = client.get(f"{KINETICS}?workflow_tool=arc").json()
    assert body["records"] == []


# ---------------------------------------------------------------------------
# Thermo filters
# ---------------------------------------------------------------------------


def test_thermo_numeric_ranges(client, thermo_corpus):
    body = client.get(f"{THERMO}?h298_max_kj_mol=0").json()
    assert _refs(body, "thermo_ref") == [thermo_corpus["cold"].public_ref]

    body = client.get(f"{THERMO}?s298_min_j_mol_k=300").json()
    assert _refs(body, "thermo_ref") == [thermo_corpus["hot"].public_ref]

    body = client.get(
        f"{THERMO}?enthalpy_formation_0k_min_kj_mol=-300"
        "&enthalpy_formation_0k_max_kj_mol=-200"
    ).json()
    assert _refs(body, "thermo_ref") == [thermo_corpus["cold"].public_ref]


def test_thermo_state_filters(client, thermo_corpus):
    for query, expected in (
        ("phase=gas", "cold"),
        ("phase=liquid", "hot"),
        ("model_kind=scalar", "cold"),
        ("model_kind=nasa7", "hot"),
        ("scientific_origin=experimental", "hot"),
        ("reference_pressure_min_bar=1.01&reference_pressure_max_bar=1.02", "hot"),
    ):
        body = client.get(f"{THERMO}?{query}").json()
        assert _refs(body, "thermo_ref") == [
            thermo_corpus[expected].public_ref
        ], query


def test_thermo_uncertainty_and_provenance_presence(client, thermo_corpus):
    body = client.get(f"{THERMO}?has_uncertainty=true").json()
    assert _refs(body, "thermo_ref") == [thermo_corpus["cold"].public_ref]

    body = client.get(f"{THERMO}?h298_uncertainty_max_kj_mol=2").json()
    assert _refs(body, "thermo_ref") == [thermo_corpus["cold"].public_ref]

    body = client.get(f"{THERMO}?has_statmech_provenance=true").json()
    assert body["records"] == []

    body = client.get(f"{THERMO}?has_literature=false").json()
    assert len(body["records"]) == 2


# ---------------------------------------------------------------------------
# Statmech filters
# ---------------------------------------------------------------------------


def test_statmech_symmetry_rotor_and_treatment_filters(client, statmech_corpus):
    for query, expected in (
        ("external_symmetry=2", "asym"),
        ("is_linear=true", "linear"),
        ("point_group=C2v", "asym"),
        ("statmech_treatment=rrho_1d", "linear"),
        ("rigid_rotor_kind=asymmetric_top", "asym"),
        ("optical_isomers=2", "linear"),
    ):
        body = client.get(f"{STATMECH}?{query}").json()
        assert _refs(body, "statmech_ref") == [
            statmech_corpus[expected].public_ref
        ], query


def test_statmech_rotational_constant_ranges(client, statmech_corpus):
    body = client.get(f"{STATMECH}?rotational_constant_a_min_cm1=5").json()
    assert _refs(body, "statmech_ref") == [statmech_corpus["asym"].public_ref]

    body = client.get(
        f"{STATMECH}?rotational_constant_b_min_cm1=1&rotational_constant_b_max_cm1=2"
    ).json()
    assert _refs(body, "statmech_ref") == [statmech_corpus["asym"].public_ref]

    # The linear rotor reports only constant A: filtering on C excludes it.
    body = client.get(f"{STATMECH}?rotational_constant_c_min_cm1=0").json()
    assert _refs(body, "statmech_ref") == [statmech_corpus["asym"].public_ref]


def test_statmech_child_row_presence_and_counts(client, statmech_corpus):
    body = client.get(f"{STATMECH}?has_torsions=true").json()
    assert _refs(body, "statmech_ref") == [statmech_corpus["asym"].public_ref]
    assert body["records"][0]["torsion_count"] == 1

    body = client.get(f"{STATMECH}?has_electronic_levels=false").json()
    assert _refs(body, "statmech_ref") == [statmech_corpus["linear"].public_ref]

    body = client.get(f"{STATMECH}?electronic_level_count_min=2").json()
    assert _refs(body, "statmech_ref") == [statmech_corpus["asym"].public_ref]
    assert body["records"][0]["electronic_level_count"] == 2

    body = client.get(f"{STATMECH}?has_frequency_scale_factor=true").json()
    assert body["records"] == []


# ---------------------------------------------------------------------------
# Calculation filters
# ---------------------------------------------------------------------------


def test_calculation_energy_and_convergence_filters(client, calculation_corpus):
    body = client.get(
        f"{CALCULATIONS}?electronic_energy_min_hartree=-77"
        "&electronic_energy_max_hartree=-76"
    ).json()
    assert _refs(body, "calculation_ref") == [
        calculation_corpus["sp"].public_ref
    ]

    body = client.get(f"{CALCULATIONS}?converged=true").json()
    assert _refs(body, "calculation_ref") == [calculation_corpus["opt"].public_ref]

    body = client.get(f"{CALCULATIONS}?calculation_type=freq").json()
    assert _refs(body, "calculation_ref") == [calculation_corpus["freq"].public_ref]


def test_calculation_freq_and_diagnostic_filters(client, calculation_corpus):
    body = client.get(f"{CALCULATIONS}?n_imag=1").json()
    assert _refs(body, "calculation_ref") == [calculation_corpus["freq"].public_ref]
    assert body["records"][0]["imag_freq_cm1"] == -1200.0

    body = client.get(f"{CALCULATIONS}?zpe_min_hartree=0.02&zpe_max_hartree=0.03").json()
    assert _refs(body, "calculation_ref") == [calculation_corpus["freq"].public_ref]

    body = client.get(f"{CALCULATIONS}?s_squared_min=0.75&s_squared_max=0.76").json()
    assert _refs(body, "calculation_ref") == [calculation_corpus["sp"].public_ref]

    # T1/D1 were never parsed for this corpus, so the axis joins away to nothing.
    body = client.get(f"{CALCULATIONS}?t1_max=0.02").json()
    assert body["records"] == []


def test_calculation_level_of_theory_and_software_filters(
    client, calculation_corpus
):
    body = client.get(f"{CALCULATIONS}?method=ccsdt").json()
    assert len(body["records"]) == 3

    body = client.get(f"{CALCULATIONS}?basis=ccpvtz&calculation_type=sp").json()
    assert _refs(body, "calculation_ref") == [calculation_corpus["sp"].public_ref]

    body = client.get(
        f"{CALCULATIONS}?lot_ref={calculation_corpus['lot'].public_ref}"
    ).json()
    assert len(body["records"]) == 3

    body = client.get(f"{CALCULATIONS}?method=nosuchmethod").json()
    assert body["records"] == []

    body = client.get(f"{CALCULATIONS}?software=gaussian").json()
    assert body["records"] == []


def test_calculation_records_carry_the_projected_scalars(
    client, calculation_corpus
):
    body = client.get(f"{CALCULATIONS}?calculation_type=sp").json()
    record = body["records"][0]
    assert record["electronic_energy_hartree"] == pytest.approx(-76.42)
    assert record["s_squared"] == pytest.approx(0.7538)
    assert record["method"] == "ccsdt"
    assert record["basis"] == "ccpvtz"
    assert record["level_of_theory_ref"] == calculation_corpus["lot"].public_ref


# ---------------------------------------------------------------------------
# Profile echo and review visibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_default_profile_is_echoed_as_exploratory(client, path):
    echo = client.get(path).json()["request"]
    assert echo["profile"] == "exploratory"
    assert echo["profile_recommendation"] == "none"
    assert echo["profile_release_ref"] is None


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_curated_profile_is_echoed(client, path):
    echo = client.get(f"{path}?profile=curated").json()["request"]
    assert echo["profile"] == "curated"
    assert echo["profile_recommendation"] == "approved_floor_only"


def test_curated_profile_narrows_the_result_set(client, db_session, thermo_corpus):
    """The floor is applied at ``visible_statuses``, so it cannot be forgotten."""
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo_corpus["cold"].id,
        status=RecordReviewStatus.approved,
    )
    db_session.flush()

    exploratory = client.get(THERMO).json()
    assert len(exploratory["records"]) == 2

    curated = client.get(f"{THERMO}?profile=curated").json()
    assert _refs(curated, "thermo_ref") == [thermo_corpus["cold"].public_ref]
    assert curated["pagination"]["total"] == 1
    assert curated["review_summary"]["approved"] == 1


def test_review_visibility_hides_rejected_unless_opted_in(
    client, db_session, thermo_corpus
):
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo_corpus["hot"].id,
        status=RecordReviewStatus.rejected,
    )
    db_session.flush()

    default = client.get(THERMO).json()
    assert _refs(default, "thermo_ref") == [thermo_corpus["cold"].public_ref]

    opted_in = client.get(f"{THERMO}?include_rejected=true").json()
    assert set(_refs(opted_in, "thermo_ref")) == {
        thermo_corpus["cold"].public_ref,
        thermo_corpus["hot"].public_ref,
    }
    assert opted_in["review_summary"]["rejected"] == 1


def test_min_review_status_gates_the_set(client, db_session, thermo_corpus):
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo_corpus["cold"].id,
        status=RecordReviewStatus.approved,
    )
    db_session.flush()
    body = client.get(f"{THERMO}?min_review_status=approved").json()
    assert _refs(body, "thermo_ref") == [thermo_corpus["cold"].public_ref]
    assert body["records"][0]["review_status"] == "approved"


def test_approved_records_sort_ahead_of_unreviewed(
    client, db_session, thermo_corpus
):
    """Ordering is by review rank in SQL, not by insertion order."""
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo_corpus["hot"].id,
        status=RecordReviewStatus.approved,
    )
    db_session.flush()
    body = client.get(THERMO).json()
    assert _refs(body, "thermo_ref")[0] == thermo_corpus["hot"].public_ref


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_internal_ids_are_hidden_by_default(client, path):
    body = client.get(path).json()
    for record in body["records"]:
        assert not any(key.endswith("_id") for key in record), record


def test_internal_ids_can_be_opted_into(client, allow_internal_ids, thermo_corpus):
    body = client.get(f"{THERMO}?include=internal_ids").json()
    assert body["request"]["include"] == ["internal_ids"]
    assert body["records"][0]["thermo_id"] > 0


# ---------------------------------------------------------------------------
# Pagination: offset and keyset
# ---------------------------------------------------------------------------


def test_offset_pagination_still_works(client, kinetics_corpus):
    first = client.get(f"{KINETICS}?limit=2").json()
    assert first["pagination"]["total"] == 3
    assert first["pagination"]["returned"] == 2
    assert first["request"]["pagination_mode"] == "offset"

    second = client.get(f"{KINETICS}?limit=2&offset=2").json()
    assert second["pagination"]["returned"] == 1
    assert set(_refs(first, "kinetics_ref")) & set(_refs(second, "kinetics_ref")) == set()


def test_keyset_traversal_returns_a_stable_complete_sequence(
    client, kinetics_corpus
):
    """Walking by cursor must visit every record exactly once, in sort order."""
    offset_order = _refs(client.get(f"{KINETICS}?limit=200").json(), "kinetics_ref")
    assert len(offset_order) == 3

    seen: list[str] = []
    body = client.get(f"{KINETICS}?limit=1").json()
    assert body["watermark"]["taken_at"] is not None
    while True:
        seen.extend(_refs(body, "kinetics_ref"))
        cursor = body["next_cursor"]
        if cursor is None:
            break
        body = client.get(f"{KINETICS}?limit=1&cursor={cursor}").json()
        assert body["request"]["pagination_mode"] == "cursor"

    assert seen == offset_order


def test_next_cursor_is_null_on_a_short_final_page(client, kinetics_corpus):
    body = client.get(f"{KINETICS}?limit=200").json()
    assert body["next_cursor"] is None


def test_watermark_excludes_records_created_after_traversal_began(
    client, db_session, kinetics_corpus
):
    """The snapshot bounds insertions — that is exactly what it promises."""
    first = client.get(f"{KINETICS}?limit=1").json()
    cursor = first["next_cursor"]
    assert cursor is not None

    latecomer = make_kinetics(
        db_session,
        reaction_entry=kinetics_corpus["low"].reaction_entry,
        a=9.9e-10,
        n=9.0,
        ea_kj_mol=999.0,
    )
    db_session.flush()

    seen = list(_refs(first, "kinetics_ref"))
    body = first
    while body["next_cursor"] is not None:
        body = client.get(f"{KINETICS}?limit=1&cursor={body['next_cursor']}").json()
        seen.extend(_refs(body, "kinetics_ref"))

    assert latecomer.public_ref not in seen
    assert len(seen) == 3


def test_a_cursor_is_bound_to_its_query(client, kinetics_corpus):
    cursor = client.get(f"{KINETICS}?limit=1").json()["next_cursor"]
    response = client.get(f"{KINETICS}?limit=1&direction=forward&cursor={cursor}")
    assert response.status_code == 422
    assert response.json()["code"] == "cursor_query_mismatch"


def test_a_cursor_is_bound_to_its_read_profile(client, kinetics_corpus):
    """Replaying an exploratory cursor under curated would traverse a different
    corpus; refusing it is the honest behaviour."""
    cursor = client.get(f"{KINETICS}?limit=1").json()["next_cursor"]
    response = client.get(f"{KINETICS}?limit=1&profile=curated&cursor={cursor}")
    assert response.status_code == 422
    assert response.json()["code"] == "cursor_query_mismatch"


# ---------------------------------------------------------------------------
# The 422 contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "query", "code"),
    [
        (KINETICS, "ea_min_kj_mol=90&ea_max_kj_mol=10", "invalid_range"),
        (KINETICS, "a_min=5&a_max=1", "invalid_range"),
        (
            KINETICS,
            "temperature_min_k=2000&temperature_max_k=300",
            "invalid_temperature_range",
        ),
        (THERMO, "h298_min_kj_mol=10&h298_max_kj_mol=-10", "invalid_range"),
        (
            STATMECH,
            "rotational_constant_a_min_cm1=9&rotational_constant_a_max_cm1=1",
            "invalid_range",
        ),
        (
            STATMECH,
            "electronic_level_count_min=5&electronic_level_count_max=2",
            "invalid_range",
        ),
        (CALCULATIONS, "t1_min=0.9&t1_max=0.1", "invalid_range"),
        (CALCULATIONS, "s_squared_min=2&s_squared_max=1", "invalid_range"),
    ],
)
def test_inverted_ranges_are_coded_422s_not_empty_pages(client, path, query, code):
    """An empty page would read as 'TCKDB has no such data'. It is a typo."""
    response = client.get(f"{path}?{query}")
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code


def test_invalid_range_names_the_real_parameters(client):
    """The message must not point at a parameter the endpoint does not have."""
    body = client.get(
        f"{CALCULATIONS}?electronic_energy_min_hartree=1"
        "&electronic_energy_max_hartree=-1"
    ).json()
    assert body["context"]["min_filter"] == "electronic_energy_min_hartree"
    assert body["context"]["max_filter"] == "electronic_energy_max_hartree"
    assert "electronic_energy_hartree_min" not in body["detail"]


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_client_sort_is_rejected(client, path):
    response = client.get(f"{path}?sort=id")
    assert response.status_code == 422
    assert response.json()["code"] == "client_sort_not_supported"


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_unknown_include_token_is_rejected(client, path):
    response = client.get(f"{path}?include=banana")
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_include_token"


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_cursor_and_offset_cannot_be_combined(client, path):
    response = client.get(f"{path}?offset=10&cursor=abc")
    assert response.status_code == 422
    assert response.json()["code"] == "cursor_offset_conflict"


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_a_garbage_cursor_is_rejected(client, path):
    response = client.get(f"{path}?cursor=not-a-real-cursor")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_cursor"


@pytest.mark.parametrize("path", ALL_ENDPOINTS)
def test_release_scoping_is_refused_here_too(client, path):
    response = client.get(f"{path}?release=2026.07.0")
    assert response.status_code == 422
    assert "release_scoping_not_implemented" in response.text


def test_limit_above_the_cap_is_rejected(client):
    assert client.get(f"{KINETICS}?limit=9999").status_code == 422


# ---------------------------------------------------------------------------
# OpenAPI exposure
# ---------------------------------------------------------------------------


def test_analytics_endpoints_are_published_with_the_profile_knob(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ALL_ENDPOINTS:
        assert path in paths, path
        names = {p["name"] for p in paths[path]["get"]["parameters"]}
        assert {"profile", "release", "cursor", "limit", "offset"} <= names, path
