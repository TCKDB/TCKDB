"""The typed method qualifiers of a level of theory replace its free-text ``keywords`` (Stage 2).

Held here:

* a level that states no qualifier hashes as the pinned Stage 2 payload (the field adds no key when unset);
* each stated qualifier, and each of its values, is a different level of theory;
* a payload that sends ``keywords`` is refused like any unknown field, on every path that carries a level;
* the stored row and the read shape carry the qualifiers;
* the deck or log is compared with the typed qualifier, warning only when both sides state a value.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session
from tckdb_schemas.enums import CoreTreatment as WireCoreTreatment
from tckdb_schemas.enums import PnoTruncation as WirePno
from tckdb_schemas.enums import RelativisticHamiltonian as WireRel
from tckdb_schemas.enums import RiApproximation as WireRi
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.db.models.common import (
    ArtifactKind,
    CalculationType,
    CoreTreatment,
    PnoTruncation,
    RelativisticHamiltonian,
    RiApproximation,
)
from app.schemas.fragments.calculation import CalculationWithResultsPayload, OptResultPayload
from app.services.calculation_parameter_extraction import extract_and_store_calculation_parameters
from app.services.calculation_resolution import (
    _level_of_theory_hash,
    _level_of_theory_payload,
    resolve_and_persist_calculation_with_results,
    resolve_level_of_theory_ref,
)
from tests.services.test_calculation_parameter_extraction import _create_species_entry, _next_inchi_key

BASE = {"method": "DLPNO-CCSD(T)", "basis": "cc-pVTZ"}

#: ``field -> every value`` (the wire enums are the ones a producer sends).
QUALIFIERS = {
    "relativistic_hamiltonian": [v.value for v in WireRel],
    "pno_truncation": [v.value for v in WirePno],
    "ri_approximation": [v.value for v in WireRi],
}


def _hash(**fields) -> str:
    return _level_of_theory_hash(LevelOfTheoryRef(**BASE, **fields))


# ---------------------------------------------------------------------------
# hash
# ---------------------------------------------------------------------------


def test_the_pinned_hash_of_a_level_with_no_qualifier():
    # Pinned: the payload after the keywords decision. A qualifier that is not stated must never move it.
    assert _level_of_theory_hash(LevelOfTheoryRef(method="b3lyp", basis="def2-tzvp")) == (
        "7f386e705fd675c5f1069df4d2a1e823de14b1f479f357e4e2243c4758a1851c"
    )


def test_an_unset_qualifier_adds_no_key_to_the_payload():
    payload = _level_of_theory_payload(LevelOfTheoryRef(**BASE))
    assert set(payload) == {
        "method", "basis", "aux_basis", "cabs_basis", "dispersion", "solvent", "solvent_model", "spin_treatment",
    }
    for field in QUALIFIERS:
        assert field not in payload
    assert "keywords" not in payload
    assert "core_treatment" not in payload


def test_stating_none_everywhere_is_the_same_as_omitting_the_fields():
    explicit = LevelOfTheoryRef(
        **BASE, core_treatment=None, relativistic_hamiltonian=None, pno_truncation=None, ri_approximation=None
    )
    assert _level_of_theory_hash(explicit) == _hash()


@pytest.mark.parametrize(("field", "value"), [(f, v) for f, values in QUALIFIERS.items() for v in values])
def test_a_stated_qualifier_is_a_different_level_from_not_stating_it(field, value):
    # Including the explicit ``none`` values: "stated non-relativistic / no RI" is not "not stated".
    assert _hash(**{field: value}) != _hash()
    assert _level_of_theory_payload(LevelOfTheoryRef(**BASE, **{field: value}))[field] == value


@pytest.mark.parametrize("field", list(QUALIFIERS))
def test_every_value_of_a_qualifier_is_its_own_level(field):
    hashes = {_hash(**{field: value}) for value in QUALIFIERS[field]}
    assert len(hashes) == len(QUALIFIERS[field])


def test_partial_frozen_core_is_a_third_core_treatment():
    hashes = {_hash(core_treatment=v) for v in ("frozen_core", "all_electron", "window")}
    assert len(hashes) == 3 and _hash() not in hashes


def test_the_qualifiers_are_independent_of_each_other():
    both = _hash(relativistic_hamiltonian="dkh2", ri_approximation="rijcosx")
    assert both != _hash(relativistic_hamiltonian="dkh2")
    assert both != _hash(ri_approximation="rijcosx")
    assert both == _hash(ri_approximation="rijcosx", relativistic_hamiltonian="dkh2")


def test_the_enum_and_its_value_hash_alike():
    assert _hash(relativistic_hamiltonian="dkh2") == _hash(relativistic_hamiltonian=WireRel.dkh2)
    assert _hash(relativistic_hamiltonian="dkh2") == _hash(relativistic_hamiltonian=RelativisticHamiltonian.dkh2)
    assert _hash(core_treatment="window") == _hash(core_treatment=WireCoreTreatment.window)
    assert _hash(core_treatment="window") == _hash(core_treatment=CoreTreatment.window)


def test_the_wire_and_database_enums_hold_the_same_values():
    for wire, db in ((WireRel, RelativisticHamiltonian), (WirePno, PnoTruncation), (WireRi, RiApproximation)):
        assert [m.value for m in wire] == [m.value for m in db]
    assert [m.value for m in WireCoreTreatment] == [m.value for m in CoreTreatment]


def test_the_values_are_the_closed_sets_the_parsers_emit():
    # One vocabulary: the typed field and the parsed parameter of the same meaning share a value set.
    from app.services.parameter_closed_values import CLOSED_VALUES

    assert set(QUALIFIERS["relativistic_hamiltonian"]) == CLOSED_VALUES["relativistic.hamiltonian"]
    assert set(QUALIFIERS["pno_truncation"]) == CLOSED_VALUES["pno.truncation"]
    assert set(QUALIFIERS["ri_approximation"]) == CLOSED_VALUES["ri.approximation"]
    assert {m.value for m in WireCoreTreatment} == CLOSED_VALUES["core.frozen"]


# ---------------------------------------------------------------------------
# wire
# ---------------------------------------------------------------------------


def test_keywords_is_refused_like_any_unknown_field():
    with pytest.raises(ValidationError) as caught:
        LevelOfTheoryRef(**BASE, keywords="DKH2")
    assert "keywords" in str(caught.value)
    with pytest.raises(ValidationError):
        LevelOfTheoryRef.model_validate({**BASE, "keywords": None})


def test_keywords_is_refused_on_a_composite_scheme_input_level():
    from tckdb_schemas.fragments.refs import CompositeSchemeInputLevel

    with pytest.raises(ValidationError):
        CompositeSchemeInputLevel(method="CCSD(T)", basis="cc-pVTZ-DK", keywords="DKH2")
    assert CompositeSchemeInputLevel(
        method="CCSD(T)", basis="cc-pVTZ-DK", relativistic_hamiltonian="dkh2"
    ).relativistic_hamiltonian == WireRel.dkh2


def test_keywords_is_refused_in_an_upload_payload_and_the_entity_schemas():
    with pytest.raises(ValidationError):
        CalculationWithResultsPayload(
            type=CalculationType.opt,
            software_release={"name": "orca", "version": "6.0"},
            level_of_theory={**BASE, "keywords": "TightPNO"},
            opt_result=OptResultPayload(converged=True),
        )
    from app.schemas.entities.level_of_theory import LevelOfTheoryUpdate

    with pytest.raises(ValidationError):
        LevelOfTheoryUpdate(keywords="x")


@pytest.mark.parametrize(
    ("field", "value"),
    [("relativistic_hamiltonian", "dkh3"), ("pno_truncation", "verytight"), ("ri_approximation", "cosx"),
     ("core_treatment", "partial")],
)
def test_a_value_outside_the_closed_set_is_refused(field, value):
    with pytest.raises(ValidationError):
        LevelOfTheoryRef(**BASE, **{field: value})


# ---------------------------------------------------------------------------
# stored row and reads
# ---------------------------------------------------------------------------


def test_the_row_stores_the_qualifiers_and_a_second_deposit_finds_it(db_session):
    ref = LevelOfTheoryRef(
        **BASE, relativistic_hamiltonian="x2c", pno_truncation="tight", ri_approximation="rijcosx",
        core_treatment="window",
    )
    row = resolve_level_of_theory_ref(db_session, ref)
    db_session.flush()
    assert (row.relativistic_hamiltonian, row.pno_truncation, row.ri_approximation, row.core_treatment) == (
        RelativisticHamiltonian.x2c, PnoTruncation.tight, RiApproximation.rijcosx, CoreTreatment.window,
    )
    assert resolve_level_of_theory_ref(db_session, ref).id == row.id
    unstated = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(**BASE))
    assert unstated.id != row.id
    assert unstated.relativistic_hamiltonian is None and unstated.ri_approximation is None


def test_the_row_has_no_keywords_column():
    from app.db.models.level_of_theory import LevelOfTheory

    assert "keywords" not in {c.key for c in LevelOfTheory.__table__.columns}


def test_the_read_shape_carries_the_qualifiers_and_no_keywords(db_session):
    from app.schemas.entities.level_of_theory import LevelOfTheoryRead
    from app.schemas.reads.scientific_level_of_theory import LevelOfTheoryCoreBlock

    row = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(**BASE, pno_truncation="normal", ri_approximation="none"))
    db_session.flush()
    read = LevelOfTheoryRead.model_validate(row)
    assert (read.pno_truncation, read.ri_approximation, read.relativistic_hamiltonian) == (
        PnoTruncation.normal, RiApproximation.none, None,
    )
    assert "keywords" not in LevelOfTheoryRead.model_fields
    assert "keywords" not in LevelOfTheoryCoreBlock.model_fields
    assert "pno_truncation" in LevelOfTheoryCoreBlock.model_fields


def test_the_snapshot_digest_ignores_a_null_qualifier_and_sees_a_stated_one():
    from app.services.snapshot_defaults import is_unchanged_default

    for column in QUALIFIERS:
        assert is_unchanged_default("level_of_theory", column, None)
        assert not is_unchanged_default("level_of_theory", column, QUALIFIERS[column][-1])


# ---------------------------------------------------------------------------
# reconciliation with the deck / log
# ---------------------------------------------------------------------------


def _calc(session: Session, *, software: str = "orca", **level):
    entry_id = _create_species_entry(session, inchi_key=_next_inchi_key("LQ"))
    upload = CalculationWithResultsPayload(
        type=CalculationType.opt,
        software_release={"name": software, "version": "6.0", "revision": "C.02"},
        level_of_theory={**BASE, **level},
        opt_result=OptResultPayload(converged=True),
    )
    return resolve_and_persist_calculation_with_results(session, upload, species_entry_id=entry_id)


_SENTENCE_NAMES = {
    "relativistic_hamiltonian": "relativistic Hamiltonian",
    "pno_truncation": "PNO truncation",
    "ri_approximation": "RI approximation",
    "core_treatment": "core treatment",
}


def _deck(keywords: str) -> str:
    return f"! {keywords}\n* xyz 0 1\nH 0 0 0\n*\n"


def _qualifier_warnings(session, calc, text):
    warnings: list = []
    extract_and_store_calculation_parameters(session, calc, text, warnings=warnings, artifact_kind=ArtifactKind.input)
    return [w for w in warnings if w.code == "level_of_theory_disagrees_with_artifact" and w.context["component"] not in ("method", "basis")]


@pytest.mark.parametrize(
    ("level", "deck", "component", "declared", "observed"),
    [
        ({"ri_approximation": "rijcosx"}, "DLPNO-CCSD(T) cc-pVTZ NoRI", "ri_approximation", "rijcosx", "none"),
        ({"pno_truncation": "tight"}, "DLPNO-CCSD(T) cc-pVTZ LoosePNO", "pno_truncation", "tight", "loose"),
        ({"relativistic_hamiltonian": "dkh2"}, "DLPNO-CCSD(T) cc-pVTZ ZORA", "relativistic_hamiltonian", "dkh2", "zora"),
        ({"core_treatment": "frozen_core"}, "DLPNO-CCSD(T) cc-pVTZ NoFrozenCore", "core_treatment", "frozen_core", "all_electron"),
    ],
)
def test_a_deck_that_contradicts_a_stated_qualifier_warns(db_conn, level, deck, component, declared, observed):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, **level)
        [warning] = _qualifier_warnings(session, calc, _deck(deck))
        assert warning.context["component"] == component
        assert warning.context["depositor_supplied"] == {"declared_value": declared, "observed_value": observed}
        assert declared not in warning.message and observed not in warning.message
        assert _SENTENCE_NAMES[component] in warning.message


@pytest.mark.parametrize(
    ("level", "deck"),
    [
        ({"ri_approximation": "rijcosx"}, "DLPNO-CCSD(T) cc-pVTZ RIJCOSX"),
        ({"pno_truncation": "tight"}, "DLPNO-CCSD(T) cc-pVTZ TightPNO"),
        ({"relativistic_hamiltonian": "dkh2"}, "DLPNO-CCSD(T) cc-pVTZ DKH"),
        ({"relativistic_hamiltonian": "x2c"}, "DLPNO-CCSD(T) cc-pVTZ X2C"),
        ({"core_treatment": "frozen_core"}, "DLPNO-CCSD(T) cc-pVTZ FrozenCore"),
        ({"ri_approximation": "rijcosx", "pno_truncation": "tight"}, "DLPNO-CCSD(T) cc-pVTZ RIJCOSX TightPNO"),
    ],
)
def test_a_deck_that_agrees_does_not_warn(db_conn, level, deck):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, **level)
        assert _qualifier_warnings(session, calc, _deck(deck)) == []


def test_a_deck_that_states_nothing_about_a_qualifier_does_not_warn(db_conn):
    # Absence is not a statement: the program default is not inferred.
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, ri_approximation="rijcosx", pno_truncation="tight", relativistic_hamiltonian="dkh2")
        assert _qualifier_warnings(session, calc, _deck("DLPNO-CCSD(T) cc-pVTZ")) == []


def test_a_level_that_states_no_qualifier_is_not_judged(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        assert _qualifier_warnings(session, calc, _deck("DLPNO-CCSD(T) cc-pVTZ NoRI LoosePNO ZORA")) == []


def test_a_qualifier_is_not_judged_against_another_programs_deck(db_conn):
    # A deck the Gaussian parser really reads: Integral=DKH2 is relativistic.hamiltonian=dkh2, which differs from
    # the ORCA-declared level's zora. The program guard is what keeps this silent.
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca", relativistic_hamiltonian="zora")
        warnings: list = []
        extract_and_store_calculation_parameters(
            session, calc, "#P CCSD(T)/cc-pVTZ Integral=DKH2 opt\n\ntitle\n\n0 1\nH 0 0 0\n\n",
            warnings=warnings, artifact_kind=ArtifactKind.input,
        )
        assert [w for w in warnings if w.context.get("component") == "relativistic_hamiltonian"] == []


def test_the_program_guard_is_what_keeps_another_programs_values_unjudged(db_conn):
    from app.services.parameter_reconciliation import level_of_theory_qualifier_warnings

    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca", ri_approximation="rijcosx")
        observed = [("ri.approximation", "none")]
        same = level_of_theory_qualifier_warnings(calc, observed, parsed_software="orca", artifact_kind=ArtifactKind.input)
        other = level_of_theory_qualifier_warnings(calc, observed, parsed_software="gaussian", artifact_kind=ArtifactKind.input)
        assert [w.context["component"] for w in same] == ["ri_approximation"]
        assert other == []


_GAUSSIAN_TWO_JOBS = (
    "#P CCSD(T,{first})/cc-pCVTZ opt\n\ntitle\n\n0 1\nH 0 0 0\n\n--Link1--\n"
    "#P CCSD(T,{second})/cc-pCVTZ opt\n\ntitle\n\n0 1\nH 0 0 0\n\n"
)


def test_a_gaussian_link1_deck_is_read_job_by_job_not_pooled(db_conn):
    # The level states a frozen core. Job 1 says Full (all electron), job 2 says FC. Both jobs fit the declared
    # method and basis, so no single job is this calculation's and nothing is judged: the other job's value must not
    # stand in, in either direction.
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="gaussian", method="CCSD(T)", basis="cc-pCVTZ", core_treatment="frozen_core")
        both = _qualifier_warnings(session, calc, _GAUSSIAN_TWO_JOBS.format(first="Full", second="FC"))
        assert both == []


def test_a_gaussian_link1_deck_judges_the_one_job_that_fits_the_declared_level(db_conn):
    deck = (
        "#P CCSD(T,Full)/cc-pCVTZ opt\n\ntitle\n\n0 1\nH 0 0 0\n\n--Link1--\n"
        "#P MP2/cc-pVTZ opt\n\ntitle\n\n0 1\nH 0 0 0\n\n"
    )
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="gaussian", method="CCSD(T)", basis="cc-pCVTZ", core_treatment="frozen_core")
        [warning] = _qualifier_warnings(session, calc, deck)
        assert warning.context["component"] == "core_treatment"
        assert warning.context["depositor_supplied"] == {"declared_value": "frozen_core", "observed_value": "all_electron"}
        agrees = _calc(session, software="gaussian", method="CCSD(T)", basis="cc-pCVTZ", core_treatment="all_electron")
        assert _qualifier_warnings(session, agrees, deck) == []


def test_an_orca_new_job_deck_is_read_job_by_job_not_pooled(db_conn):
    # Job 1 (a different method) states RIJCOSX; the declared level is job 2's, which states no RI. Pooled, the
    # level's rijk would be judged against job 1's rijcosx. Per job, job 2 says nothing about RI: silent.
    deck = _deck("B3LYP def2-SVP RIJCOSX Opt") + "$new_job\n" + _deck("DLPNO-CCSD(T) cc-pVTZ TightPNO")
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, ri_approximation="rijk")
        assert _qualifier_warnings(session, calc, deck) == []
        # The qualifier the fitting job does state is still judged.
        tight = _calc(session, ri_approximation="rijk", pno_truncation="loose")
        [warning] = _qualifier_warnings(session, tight, deck)
        assert warning.context["component"] == "pno_truncation"
        # And a contradiction inside the fitting job warns.
        own = _deck("B3LYP def2-SVP Opt") + "$new_job\n" + _deck("DLPNO-CCSD(T) cc-pVTZ RIJCOSX")
        wrong = _calc(session, ri_approximation="rijk")
        [clash] = _qualifier_warnings(session, wrong, own)
        assert clash.context["component"] == "ri_approximation"


def test_a_multi_job_deck_with_no_single_fitting_job_judges_nothing(db_conn):
    deck = _deck("DLPNO-CCSD(T) cc-pVTZ RIJCOSX") + "$new_job\n" + _deck("DLPNO-CCSD(T) cc-pVTZ NoRI")
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, ri_approximation="rijk")
        assert _qualifier_warnings(session, calc, deck) == []


def test_each_disagreeing_qualifier_warns_once(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, ri_approximation="rijcosx", pno_truncation="tight")
        got = _qualifier_warnings(session, calc, _deck("DLPNO-CCSD(T) cc-pVTZ NoRI LoosePNO"))
        assert sorted(w.context["component"] for w in got) == ["pno_truncation", "ri_approximation"]
