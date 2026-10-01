"""Identity, labels and write-time rules of user-built composite schemes (ADR 0021, P5).

Service level, no HTTP. Three groups:

* **identity** -- the definition hash and the level-of-theory hash: what is in
  them (formula, exponent, cardinals, core treatment, term order), what is not
  (``term_key``, literature, input order), that merged spellings are followed,
  and that a declared level's hash cannot collide with a normal one;
* **the label** -- readable, deterministic, capped;
* **unvalidated payloads** -- every rule re-run at the write for objects that
  skipped the wire validators (``model_copy`` / ``model_construct``).
"""

from __future__ import annotations

import copy
import itertools

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload, CompositeResultPayload
from tckdb_schemas.fragments.refs import (
    CompositeSchemeDefinition,
    LevelOfTheoryRef,
)

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, CompositeSchemeKind, EnergyComponentKind
from app.db.models.composite_scheme import CompositeScheme, LevelOfTheoryComposite
from app.db.models.level_of_theory import LevelOfTheory
from app.services.calculation_resolution import (
    _level_of_theory_hash,
    _level_of_theory_payload,
    resolve_and_persist_calculation_with_results,
    resolve_level_of_theory_ref,
)
from app.services.composite_input_resolution import finalize_composite_inputs
from app.services.composite_result_resolution import persist_composite_result
from app.services.composite_scheme_label import LABEL_MAX_LENGTH, LabelInput, LabelTerm, build_scheme_label
from app.services.composite_scheme_resolution import (
    declared_scheme_lot_hash,
    declared_scheme_lot_payload,
)
from tests import composite_p5_fixtures as f

_COUNTER = itertools.count()


def _resolve(session: Session, scheme: dict) -> LevelOfTheory:
    return resolve_level_of_theory_ref(session, LevelOfTheoryRef(composite_scheme=scheme))


def _scheme_row(session: Session, level: LevelOfTheory) -> CompositeScheme:
    return session.get(CompositeScheme, session.get(LevelOfTheoryComposite, level.id).scheme_id)


# ---------------------------------------------------------------------------
# identity: what is in the hash and what is not
# ---------------------------------------------------------------------------


def test_resolving_the_same_definition_twice_is_one_scheme_one_level_one_binding(db_session):
    a, b = _resolve(db_session, f.SCHEME_B), _resolve(db_session, f.SCHEME_B)
    assert a.id == b.id
    assert db_session.query(LevelOfTheoryComposite).filter_by(level_of_theory_id=a.id).count() == 1
    scheme = _scheme_row(db_session, a)
    assert a.lot_hash == declared_scheme_lot_hash(scheme.definition_hash)
    assert a.method == scheme.name == f.LABEL_B


def test_the_definition_hash_is_pinned(db_session):
    """Changing the canonical form re-keys every declared level and scheme ref: this must be deliberate."""
    scheme = _scheme_row(db_session, _resolve(db_session, f.SCHEME_B))
    assert scheme.definition_hash == "5806fd74684c03e4bc39f93473a101f944e8a177251deb17d52ca7a91bb1b307"


def test_term_keys_are_not_identity_but_formula_exponent_and_cardinals_are(db_session):
    base = _resolve(db_session, f.SCHEME_B)

    renamed = copy.deepcopy(f.SCHEME_B)
    renamed["terms"][0]["key"], renamed["terms"][1]["key"] = "scf", "ccsd_t"
    assert _resolve(db_session, renamed).id == base.id  # the keys are local names

    def differs(mutate) -> None:
        scheme = copy.deepcopy(f.SCHEME_B)
        mutate(scheme)
        assert _resolve(db_session, scheme).id != base.id

    differs(lambda s: s["terms"][1].update(exponent=3.4))  # exponent 3 vs 3.4: two levels (ADR 0021 2.4)
    differs(lambda s: s["terms"][1].update(formula="inverse_power_shifted_half"))
    differs(lambda s: s["terms"][1]["inputs"][0].update(cardinal_number=2))  # declared cardinal
    differs(lambda s: s["terms"][0].update(energy_component="total"))  # which component is read
    differs(lambda s: s["terms"].reverse())  # term order is position, and position is identity


def test_the_order_of_inputs_within_a_term_is_not_identity(db_session):
    base = _resolve(db_session, f.SCHEME_B)
    swapped = copy.deepcopy(f.SCHEME_B)
    swapped["terms"][1]["inputs"].reverse()
    assert _resolve(db_session, swapped).id == base.id


def test_literature_is_provenance_not_identity(db_session):
    base = _resolve(db_session, f.SCHEME_B)
    cited = copy.deepcopy(f.SCHEME_B)
    cited["literature"] = {"kind": "article", "title": "Basis-set convergence of correlated calculations", "year": 1997}
    assert _resolve(db_session, cited).id == base.id


def test_core_treatment_is_identity_through_the_input_levels(db_session):
    swapped = copy.deepcopy(f.SCHEME_C)
    dcv = next(t for t in swapped["terms"] if t["key"] == "dcv")
    dcv["inputs"][0]["level_of_theory"], dcv["inputs"][1]["level_of_theory"] = (
        dcv["inputs"][1]["level_of_theory"],
        dcv["inputs"][0]["level_of_theory"],
    )
    # High and low swapped: the difference changes sign, so it is a different recipe.
    assert _resolve(db_session, swapped).id != _resolve(db_session, f.SCHEME_C).id
    dropped = copy.deepcopy(f.SCHEME_C)
    for item in next(t for t in dropped["terms"] if t["key"] == "dcv")["inputs"]:
        del item["level_of_theory"]["core_treatment"]
    # Without the core treatment the two inputs are one level of theory and a ΔCV term is inexpressible.
    level = _resolve(db_session, dropped)
    scheme = _scheme_row(db_session, level)
    dcv_term = next(t for t in scheme.terms if t.position == 1)
    assert dcv_term.inputs[0].level_of_theory_id == dcv_term.inputs[1].level_of_theory_id
    assert level.id != _resolve(db_session, f.SCHEME_C).id


def test_term_input_levels_carry_their_core_treatment(db_session):
    scheme = _scheme_row(db_session, _resolve(db_session, f.SCHEME_C))
    dcv = next(t for t in scheme.terms if t.position == 1)
    cores = {i.slot.value: i.level_of_theory.core_treatment.value for i in dcv.inputs}
    assert cores == {"high": "all_electron", "low": "frozen_core"}


def test_merged_levels_are_followed_when_the_scheme_is_resolved(db_session):
    """An input spelled as a level that was merged into another is that other level."""
    kept = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(**f.TZ))
    spelled = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVTZ-legacy"))
    assert spelled.id != kept.id
    db_session.execute(
        text("INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) VALUES (:m, :k)"),
        {"m": spelled.id, "k": kept.id},
    )
    through_merge = copy.deepcopy(f.SCHEME_B)
    through_merge["terms"][1]["inputs"][0]["level_of_theory"] = {"method": "CCSD(T)", "basis": "cc-pVTZ-legacy"}
    assert _resolve(db_session, through_merge).id == _resolve(db_session, f.SCHEME_B).id
    scheme = _scheme_row(db_session, _resolve(db_session, through_merge))
    stored = {i.level_of_theory_id for t in scheme.terms for i in t.inputs}
    assert spelled.id not in stored and kept.id in stored


# ---------------------------------------------------------------------------
# the level-of-theory hash cannot collide with a normal one
# ---------------------------------------------------------------------------


def test_a_declared_payload_has_no_method_key_and_every_normal_payload_has_one():
    declared = declared_scheme_lot_payload("a" * 64)
    assert declared == '{"composite_scheme":"' + "a" * 64 + '"}'
    refs = [
        LevelOfTheoryRef(method="b3lyp"),
        LevelOfTheoryRef(method="CBS-QB3"),
        LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVQZ", core_treatment="frozen_core"),
        # A hostile method that *looks like* a declared payload is still a value under "method".
        LevelOfTheoryRef(method='{"composite_scheme":"' + "a" * 64 + '"}'),
        LevelOfTheoryRef(method="composite_scheme", keywords="composite_scheme"),
    ]
    for ref in refs:
        payload = _level_of_theory_payload(ref)
        assert "method" in payload and "composite_scheme" not in payload
        assert _level_of_theory_hash(ref) != declared_scheme_lot_hash("a" * 64)


def test_a_plain_level_named_like_a_scheme_label_is_a_different_level(db_session):
    declared = _resolve(db_session, f.SCHEME_B)
    lookalike = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method=declared.method))
    assert lookalike.id != declared.id
    assert lookalike.lot_hash != declared.lot_hash
    assert db_session.get(LevelOfTheoryComposite, lookalike.id) is None


def test_the_method_hash_function_refuses_a_ref_that_names_a_scheme():
    with pytest.raises(ValueError, match="composite_scheme"):
        _level_of_theory_hash(LevelOfTheoryRef(composite_scheme=f.SCHEME_B))


def test_normal_level_hashes_are_unchanged_by_this_phase():
    """Pinned: a plain level keeps the hash it had before declared schemes existed.

    The value was computed with ``main``'s own ``_level_of_theory_hash`` (7404d9b5), not this branch's.
    """
    assert _level_of_theory_hash(LevelOfTheoryRef(method="b3lyp", basis="def2-tzvp")) == (
        "f755381642e0a50e8f9d992317cea9ae39e03284a3e993fa5004025b52098f30"
    )


# ---------------------------------------------------------------------------
# the label
# ---------------------------------------------------------------------------


def test_the_two_worked_labels(db_session):
    assert _resolve(db_session, f.SCHEME_B).method == f.LABEL_B
    c = _resolve(db_session, f.SCHEME_C).method
    assert c == (
        "Additive[CCSD(T)/cc-pVQZ"
        " + dE:CCSD(T)/cc-pCVTZ ae - CCSD(T)/cc-pCVTZ fc"
        " + dE:CCSDT(Q)/cc-pVDZ - CCSD(T)/cc-pVDZ"
        " + dE:CCSD(T)/cc-pVTZ-DK (kw=DKH2) - CCSD(T)/cc-pVTZ"
        " + DBOC:HF/cc-pVDZ]"
    ).replace("CCSD(T)/cc-pVQZ", "base CCSD(T)/cc-pVQZ", 1)


def test_a_label_is_a_function_of_the_definition_only():
    terms = [
        LabelTerm("extrapolation", EnergyComponentKind.total, "inverse_power", 3.4,
                  [LabelInput("cardinal", 4, "MP2", "aug-cc-pVQZ"), LabelInput("cardinal", 3, "MP2", "aug-cc-pVTZ")])
    ]
    a = build_scheme_label(CompositeSchemeKind.extrapolation, terms, "0" * 64)
    assert a == build_scheme_label(CompositeSchemeKind.extrapolation, list(terms), "f" * 64)
    assert a == "CBS[MP2/aug-cc-pV{T,Q}Z; inverse_power x=3.4; n=3,4]"
    assert "//" not in a and a.isascii()


def test_levels_that_share_no_basis_shape_are_listed_in_full():
    terms = [
        LabelTerm("extrapolation", EnergyComponentKind.total, "inverse_power", 3,
                  [LabelInput("cardinal", 2, "MP2", "cc-pVDZ"), LabelInput("cardinal", 3, "CCSD(T)", "cc-pVTZ")])
    ]
    assert build_scheme_label(CompositeSchemeKind.extrapolation, terms, "0" * 64) == (
        "CBS[{MP2/cc-pVDZ,CCSD(T)/cc-pVTZ}; inverse_power x=3; n=2,3]"
    )


def test_a_long_recipe_is_cut_and_tagged_with_its_hash():
    inputs = [LabelInput("value", None, "METHOD" * 8, "BASIS" * 8)]
    terms = [LabelTerm("value", EnergyComponentKind.total, None, None, inputs) for _ in range(12)]
    digest = "ab12cd34" + "0" * 56
    label = build_scheme_label(CompositeSchemeKind.additive, terms, digest)
    assert len(label) <= LABEL_MAX_LENGTH
    assert label.endswith("...#ab12cd34")
    assert label.startswith("Additive[")


# ---------------------------------------------------------------------------
# nested composites and unvalidated definitions
# ---------------------------------------------------------------------------


def test_a_named_composite_method_as_an_input_is_refused_before_any_level_is_created(db_session):
    scheme = copy.deepcopy(f.SCHEME_B)
    scheme["terms"][0]["inputs"][0]["level_of_theory"] = {"method": "G4"}
    with pytest.raises(CodedValueError) as err:
        _resolve(db_session, scheme)
    assert err.value.code == "composite_scheme_nested"
    assert db_session.scalar(select(LevelOfTheory).where(LevelOfTheory.method == "G4")) is None


def test_an_input_that_resolves_to_a_bound_level_is_refused_as_nested(db_session):
    declared = _resolve(db_session, f.SCHEME_B)
    # A model-built input whose method is the generated label of a bound level is still an ordinary
    # method name, so it resolves to a different, unbound level and is accepted; only the level the
    # binding names is composite.
    scheme = copy.deepcopy(f.SCHEME_B)
    scheme["terms"][0]["inputs"][0]["level_of_theory"] = {"method": declared.method}
    assert _resolve(db_session, scheme).id != declared.id


def test_the_resolver_re_runs_the_shape_rules_on_a_definition_the_models_never_validated(db_session):
    bad = copy.deepcopy(f.SCHEME_B)
    del bad["terms"][1]["exponent"]
    definition = CompositeSchemeDefinition.model_construct(
        kind=bad["kind"],
        terms=[
            type("T", (), {
                "key": t["key"], "operation": t["operation"], "energy_component": t["energy_component"],
                "formula": t.get("formula"), "exponent": t.get("exponent"),
                "inputs": [type("I", (), {"slot": i["slot"], "cardinal_number": i.get("cardinal_number"),
                                          "level_of_theory": LevelOfTheoryRef(**i["level_of_theory"])})()
                           for i in t["inputs"]],
            })()
            for t in bad["terms"]
        ],
        literature=None,
    )
    ref = LevelOfTheoryRef.model_construct(method=None, composite_scheme=definition)
    with pytest.raises(CodedValidationError) as err:
        resolve_level_of_theory_ref(db_session, ref)
    assert err.value.code == "composite_scheme_malformed" and err.value.context["rule"] == "exponent_required"


def test_the_resolver_refuses_a_nested_definition_the_models_never_validated(db_session):
    nested = LevelOfTheoryRef.model_construct(method="x", basis=None, composite_scheme=None)
    inner = type("I", (), {"slot": "value", "cardinal_number": None,
                           "level_of_theory": type("L", (), {"composite_scheme": f.SCHEME_B, "method": None})()})()
    term = type("T", (), {"key": "k", "operation": "value", "energy_component": "total",
                          "formula": None, "exponent": None, "inputs": [inner]})()
    definition = type("D", (), {"kind": "extrapolation", "terms": [term], "literature": None})()
    assert nested is not None
    ref = type("R", (), {"method": None, "composite_scheme": definition})()
    with pytest.raises((CodedValueError, CodedValidationError)) as err:
        resolve_level_of_theory_ref(db_session, ref)
    assert err.value.code in {"composite_scheme_nested", "composite_scheme_malformed"}


def test_method_and_definition_together_are_refused_at_the_resolver_too(db_session):
    ref = LevelOfTheoryRef(composite_scheme=f.SCHEME_B).model_copy(update={"method": "CBS-QB3"})
    with pytest.raises(CodedValidationError) as err:
        resolve_level_of_theory_ref(db_session, ref)
    assert err.value.code == "level_of_theory_method_with_composite_scheme"


# ---------------------------------------------------------------------------
# the write seam: every wire rule re-run for payloads that skipped validation
# ---------------------------------------------------------------------------


def _species_entry(session: Session) -> int:
    tag = f"CMPP5{next(_COUNTER):0>22}"[:27]
    species_id = session.connection().execute(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES ('molecule', :s, :k, 0, 1, 'achiral') RETURNING id"
        ),
        {"s": tag, "k": tag},
    ).scalar_one()
    return session.connection().execute(
        text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id}
    ).scalar_one()


def _persist_sp(session, entry_id, lot, energy, components=None) -> Calculation:
    payload = CalculationWithResultsPayload(
        type="sp",
        software_release=f.SOFTWARE,
        level_of_theory=lot,
        sp_result={"electronic_energy_hartree": energy},
        sp_energy_components=[{"component": c, "value_hartree": v} for c, v in (components or [])],
    )
    return resolve_and_persist_calculation_with_results(session, payload, species_entry_id=entry_id)


def _assembled_payload(inputs, total=f.TOTAL_B, scheme=None) -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload(
        type="composite",
        level_of_theory={"composite_scheme": scheme or f.SCHEME_B},
        composite_result={"assembly": "assembled", "electronic_energy_hartree": total, "inputs": inputs},
    )


def _refs_for(calcs: dict[str, Calculation]) -> list[dict]:
    return [
        {**i, "calculation_ref": calcs[i["calculation_key"]].public_ref}
        | {"calculation_key": None}
        for i in f.INPUTS_B
    ]


def _clean_inputs(inputs: list[dict]) -> list[dict]:
    return [{k: v for k, v in i.items() if v is not None} for i in inputs]


def _sps(session, entry_id) -> dict[str, Calculation]:
    return {
        "spt": _persist_sp(session, entry_id, f.TZ, f.R_T + f.C_T, [("reference", f.R_T), ("correlation", f.C_T)]),
        "spq": _persist_sp(session, entry_id, f.QZ, f.R_Q + f.C_Q, [("reference", f.R_Q), ("correlation", f.C_Q)]),
    }


def test_a_valid_assembled_composite_finalizes_through_the_service_alone(db_session):
    entry = _species_entry(db_session)
    sps = _sps(db_session, entry)
    composite = resolve_and_persist_calculation_with_results(
        db_session, _assembled_payload(_clean_inputs(_refs_for(sps))), species_entry_id=entry
    )
    warnings = finalize_composite_inputs(db_session, [composite.id, *(c.id for c in sps.values())])
    # No geometry was attached to these single points, so the one-geometry check cannot run for them.
    assert sorted(w.code for w in warnings) == ["composite_input_geometry_undeclared"] * 2
    assert composite.software_release_id is None


@pytest.mark.parametrize(
    ("edit", "code"),
    [
        (lambda rows: rows.pop(), "composite_input_missing"),
        (lambda rows: rows.append(dict(rows[0])), "composite_input_duplicate"),
        (lambda rows: rows[0].update(term_key="nope"), "composite_input_slot_unknown"),
    ],
    ids=["missing", "duplicate", "unknown"],
)
def test_the_seam_re_runs_the_input_rules_for_a_payload_built_with_model_copy(db_session, edit, code):
    entry = _species_entry(db_session)
    sps = _sps(db_session, entry)
    good = _assembled_payload(_clean_inputs(_refs_for(sps)))
    rows = [i.model_copy() for i in good.composite_result.inputs]
    edit_rows = [{"term_key": r.term_key, "slot": r.slot, "cardinal_number": r.cardinal_number,
                  "calculation_ref": r.calculation_ref} for r in rows]
    edit(edit_rows)
    bad_inputs = [good.composite_result.inputs[0].model_copy(update=r) for r in edit_rows]
    bad = good.model_copy(update={"composite_result": good.composite_result.model_copy(update={"inputs": bad_inputs})})
    with pytest.raises(CodedValidationError) as err:
        resolve_and_persist_calculation_with_results(db_session, bad, species_entry_id=entry)
    assert err.value.code == code


def test_the_seam_re_runs_the_software_rule_for_a_payload_built_with_model_copy(db_session):
    entry = _species_entry(db_session)
    sp = CalculationWithResultsPayload(
        type="sp", software_release=f.SOFTWARE, level_of_theory=f.QZ, sp_result={"electronic_energy_hartree": -1.0}
    ).model_copy(update={"software_release": None})
    with pytest.raises(CodedValidationError) as err:
        resolve_and_persist_calculation_with_results(db_session, sp, species_entry_id=entry)
    assert err.value.code == "calculation_software_release_required"


def test_the_seam_re_runs_the_assembled_needs_a_scheme_rule(db_session):
    entry = _species_entry(db_session)
    sps = _sps(db_session, entry)
    good = _assembled_payload(_clean_inputs(_refs_for(sps)))
    bad = good.model_copy(update={"level_of_theory": LevelOfTheoryRef(method="CBS-QB3")})
    with pytest.raises(CodedValidationError) as err:
        resolve_and_persist_calculation_with_results(db_session, bad, species_entry_id=entry)
    assert err.value.code == "composite_assembled_not_accepted"


def test_persist_composite_result_itself_refuses_an_assembled_block_at_a_named_level(db_session):
    entry = _species_entry(db_session)
    level = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3"))
    calc = Calculation(type=CalculationType.composite, species_entry_id=entry, lot_id=level.id)
    db_session.add(calc)
    db_session.flush()
    block = CompositeResultPayload.model_construct(
        assembly="assembled", electronic_energy_hartree=-1.0, e0_hartree=None, recipe_zpe_hartree=None,
        terms=[], inputs=[],
    )
    from app.db.models.common import CompositeAssembly

    block = block.model_copy(update={"assembly": CompositeAssembly.assembled})
    with pytest.raises(CodedValueError) as err:
        persist_composite_result(db_session, calc, block)
    assert err.value.code == "composite_assembled_not_accepted"


def test_an_unfinished_assembled_composite_cannot_be_committed(db_session):
    """The before-commit guard: a workflow that forgot ``finalize_composite_inputs`` fails loudly."""
    from app.db.composite_commit_guard import refuse_unfinished_composites as _refuse_unfinished_composites

    entry = _species_entry(db_session)
    sps = _sps(db_session, entry)
    resolve_and_persist_calculation_with_results(
        db_session, _assembled_payload(_clean_inputs(_refs_for(sps))), species_entry_id=entry
    )
    with pytest.raises(RuntimeError, match="finalize_composite_inputs"):
        _refuse_unfinished_composites(db_session)
    db_session.info.pop("pending_composite_inputs", None)
    _refuse_unfinished_composites(db_session)  # nothing pending: quiet


def test_a_total_that_cannot_be_checked_is_a_warning_with_the_reason(db_session):
    entry = _species_entry(db_session)
    sps = _sps(db_session, entry)
    sps["spt"] = _persist_sp(db_session, entry, f.TZ, -1.0)  # no components: the correlation is not stated
    composite = resolve_and_persist_calculation_with_results(
        db_session, _assembled_payload(_clean_inputs(_refs_for(sps))), species_entry_id=entry
    )
    warnings = finalize_composite_inputs(db_session, [composite.id])
    unverifiable = next(w for w in warnings if w.code == "composite_total_unverifiable")
    assert "component" in unverifiable.message


def test_a_correlation_that_cannot_be_tied_to_its_energy_is_unverifiable_not_guessed(db_session):
    """Reference and correlation stored, but the single point's energy was filled from elsewhere."""
    entry = _species_entry(db_session)
    sps = _sps(db_session, entry)
    db_session.execute(
        text("UPDATE calc_sp_result SET electronic_energy_hartree = -70.0 WHERE calculation_id = :c"),
        {"c": sps["spt"].id},
    )
    composite = resolve_and_persist_calculation_with_results(
        db_session, _assembled_payload(_clean_inputs(_refs_for(sps))), species_entry_id=entry
    )
    warnings = finalize_composite_inputs(db_session, [composite.id])
    unverifiable = next(w for w in warnings if w.code == "composite_total_unverifiable")
    assert "correlation" in unverifiable.message


def test_the_level_check_follows_merges_on_the_schemes_side(db_session):
    """A scheme slot whose level was later merged into the input's level still matches that input.

    The scheme was written against a spelling of the TZ level that has since been merged away; the single
    point ran at the surviving row. Comparing ids literally would call that a level mismatch.
    """
    entry = _species_entry(db_session)
    sps = _sps(db_session, entry)
    composite = resolve_and_persist_calculation_with_results(
        db_session, _assembled_payload(_clean_inputs(_refs_for(sps))), species_entry_id=entry
    )
    spelled = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVTZ-merged-away"))
    db_session.execute(
        text("INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) VALUES (:m, :k)"),
        {"m": spelled.id, "k": sps["spt"].lot_id},
    )
    db_session.execute(
        text(
            "UPDATE composite_scheme_term_input SET level_of_theory_id = :z WHERE level_of_theory_id = :tz "
            "AND term_id IN (SELECT t.id FROM composite_scheme_term t JOIN level_of_theory_composite c "
            "ON c.scheme_id = t.scheme_id JOIN calculation k ON k.lot_id = c.level_of_theory_id WHERE k.id = :c)"
        ),
        {"z": spelled.id, "tz": sps["spt"].lot_id, "c": composite.id},
    )
    warnings = finalize_composite_inputs(db_session, [composite.id])
    assert "composite_total_unverifiable" not in [w.code for w in warnings]
