"""The worked-case generators of the demonstration snapshot (``scripts.paper.snapshot_cases``).

Each runs on rows seeded through the test factories. The tests pin the shape and the rules: subjects found by
content, rows ordered by content, no database id, refs only as values, and the reconciliation arithmetic.
"""

from __future__ import annotations

import hashlib
import json
import math
from types import SimpleNamespace

from app.chemistry.arrhenius import GAS_CONSTANT_J_MOL_K
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    ArrheniusAUnits,
    CalculationType,
    NetworkKineticsModelKind,
    NetworkStateKind,
    PhaseKind,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.services.deposit.expected_outputs import render_json
from app.services.record_review import set_record_review_status
from app.services.release.curation import add_selection, create_release, publish_release, resolve_curation_policy
from app.services.release.manifest import freeze_manifest
from scripts.paper import generators, snapshot_cases, snapshot_generators
from tests.scripts.test_snapshot_generators import _no_integer_ids
from tests.services.release._attest import deposit_and_attest
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_network_kinetics_chebyshev,
    attach_network_state_participant,
    attach_thermo_nasa,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    make_workflow_tool_release,
    set_review,
)


def _ids_free(document) -> bool:
    return not _no_integer_ids(json.loads(render_json(document)))


def _reaction(db_session, reactants, products):
    """A reaction entry of species with these SMILES (multiplicities given as (smiles, multiplicity))."""
    r_species = [make_species(db_session, smiles=s, multiplicity=m) for s, m in reactants]
    p_species = [make_species(db_session, smiles=s, multiplicity=m) for s, m in products]
    reaction = make_chem_reaction(db_session, reactants=r_species, products=p_species)
    entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[make_species_entry(db_session, species=s) for s in r_species],
        product_entries=[make_species_entry(db_session, species=s) for s in p_species],
    )
    return entry, r_species, p_species


def test_use_case_reports_k_at_300_k_the_ts_frequency_and_no_irc(db_session):
    entry, _, _ = _reaction(db_session, [("C", 1), ("[OH]", 2)], [("O", 1), ("[CH3]", 2)])
    computed = make_kinetics(
        db_session,
        reaction_entry=entry,
        a=1459.78,
        a_units=ArrheniusAUnits.cm3_mol_s,
        n=3.46761,
        ea_kj_mol=8.50629,
        tmin_k=300.0,
        tmax_k=3000.0,
    )
    make_kinetics(db_session, reaction_entry=entry, scientific_origin=ScientificOriginKind.estimated)
    ts_entry = make_transition_state_entry(
        db_session, transition_state=make_transition_state(db_session, reaction_entry=entry, label="TS0")
    )
    freq = make_calculation(db_session, type=CalculationType.freq, transition_state_entry_id=ts_entry.id)
    attach_freq_result(db_session, calculation=freq, frequencies_cm1=[-1187.0, 300.0, 900.0])

    out = snapshot_cases.use_case_ch4_oh(db_session)
    (row,) = out["computed_records"]
    assert row["kinetics_ref"] == computed.public_ref
    expected = 1459.78 * 300.0**3.46761 * math.exp(-8506.29 / (GAS_CONSTANT_J_MOL_K * 300.0))
    assert math.isclose(row["k_at_300_k"]["k"], expected, rel_tol=1e-5)
    assert row["k_at_300_k"]["units"] == "cm3_mol_s"
    assert row["k_at_300_k"]["within_fit_temperature_range"] is True
    (ts,) = row["transition_states"]
    assert ts["frequency_calculations"][0]["imaginary_modes_cm1"] == [-1187.0]
    assert ts["irc_outcome"] == "no IRC calculation and no IRC validation evidence deposited"
    assert [r["scientific_origin"] for r in out["other_origin_records_of_this_reaction"]] == ["estimated"]
    assert _ids_free(out)


def test_use_case_flags_300_k_outside_the_fit_range(db_session):
    entry, _, _ = _reaction(db_session, [("C", 1), ("[OH]", 2)], [("O", 1), ("[CH3]", 2)])
    make_kinetics(db_session, reaction_entry=entry, tmin_k=500.0, tmax_k=2000.0)
    (row,) = snapshot_cases.use_case_ch4_oh(db_session)["computed_records"]
    assert row["k_at_300_k"]["within_fit_temperature_range"] is False
    assert row["k_at_300_k"]["k"] is not None


def test_chemkin_reconciliation_arithmetic_adds_up_on_the_file_side(db_session):
    out = snapshot_cases.chemkin_reconciliation(db_session)
    arithmetic = out["arithmetic"]
    assert out["reactions_in_file"] == 3002
    assert arithmetic["reactions_in_file"] == (
        arithmetic["minus_lines_summed_into_a_partner_record"] + arithmetic["equals_rate_records"]
    )
    assert out["rate_records_built_by_adapter"] == arithmetic["equals_rate_records"]
    summed = [r for r in out["merged_lines"] if r["cause"] == "duplicate_pair_summed"]
    assert len(summed) == arithmetic["minus_lines_summed_into_a_partner_record"]
    assert all(r["merged_into_line"] < r["line"] for r in out["merged_lines"])
    assert [r["line"] for r in out["merged_lines"]] == sorted(r["line"] for r in out["merged_lines"])
    assert out["model_deposited"] is False and arithmetic["adds_up"] is False
    assert _ids_free(out)


def test_chemkin_merge_causes_name_each_reason():
    def line(reactants, products, *, duplicate=False, chebyshev=None, falloff=False):
        return SimpleNamespace(
            reactant_names=reactants, product_names=products, duplicate=duplicate, chebyshev=chebyshev,
            plog=[], troe=None, sri=None, is_falloff=falloff, is_third_body=False,
        )

    plain = line(["A", "B"], ["C"])
    assert snapshot_cases._merge_cause(plain, line(["A", "B"], ["C"]))[0] == "undeclared_repeat"
    assert snapshot_cases._merge_cause(plain, line(["A", "B"], ["C"], chebyshev=object(), falloff=True))[0] == (
        "same_reaction_two_rate_forms"
    )
    dup = line(["A", "B"], ["C"], duplicate=True, chebyshev=object())
    assert snapshot_cases._merge_cause(dup, dup)[0] == "duplicate_pair_not_summed"
    assert snapshot_cases._merge_cause(plain, line(["A2", "B"], ["C"]))[0] == "different_labels_same_species"


def test_network_reports_channels_and_k_from_the_stored_chebyshev_fit(db_session):
    network = make_network(db_session, name="N2H3")
    solve = make_network_solve(db_session, network=network)
    wells = {}
    for smiles in ("[N-][NH3+]", "[NH]N"):
        state = make_network_state(db_session, network=network, kind=NetworkStateKind.well, composition_hash=hashlib.sha256(smiles.encode()).hexdigest())
        species = make_species(db_session, smiles=smiles, multiplicity=2)
        attach_network_state_participant(db_session, state=state, species_entry=make_species_entry(db_session, species=species))
        wells[smiles] = state
    channel = make_network_channel(db_session, network=network, source_state=wells["[N-][NH3+]"], sink_state=wells["[NH]N"])
    nk = make_network_kinetics(
        db_session, channel=channel, solve=solve, model_kind=NetworkKineticsModelKind.chebyshev,
        rate_units=ArrheniusAUnits.per_s, stores_log10_k=True,
    )
    attach_network_kinetics_chebyshev(db_session, kinetics=nk, n_temperature=1, n_pressure=1, coefficients={"coeffs": [[5.0]]})

    out = snapshot_cases.network_n2h3(db_session)
    row = next(n for n in out["networks"] if n["name"] == "N2H3")
    assert row["wells"] == ["[N-][NH3+]", "[NH]N"]
    assert row["channels_by_kind"] == {"isomerization": 1}
    assert row["kinetics_forms_by_model_kind"] == {"chebyshev": 1}
    (point,) = row["nh3n_to_n2h3"]
    assert point["k"] == 1.0e5 and point["units"] == "per_s" and point["in_fit_range"] is True
    assert _ids_free(out)


def test_caught_error_excludes_the_rejected_record_and_finds_the_rerun_under_another_smiles(db_session):
    inchi = "IVJRWJRXATUQHM-UHFFFAOYSA-N"
    reactants = [make_species(db_session, smiles="CC(C)C=O"), make_species(db_session, smiles="[H]", multiplicity=2)]
    h2 = make_species(db_session, smiles="[H][H]")
    old_product = make_species(db_session, smiles="CC(C)=C[O]", inchi_key=inchi, multiplicity=2)
    new_product = make_species(db_session, smiles="C[C](C)C=O", inchi_key=inchi, multiplicity=2)
    reactant_entries = [make_species_entry(db_session, species=s) for s in reactants]
    h2_entry = make_species_entry(db_session, species=h2)
    kinetics = {}
    for name, product in (("old", old_product), ("rerun", new_product)):
        reaction = make_chem_reaction(db_session, reactants=reactants, products=[product, h2])
        entry = make_reaction_entry(
            db_session,
            reaction=reaction,
            reactant_entries=reactant_entries,
            product_entries=[make_species_entry(db_session, species=product), h2_entry],
        )
        kinetics[name] = make_kinetics(db_session, reaction_entry=entry)
    set_review(db_session, record_type=SubmissionRecordType.kinetics, record_id=kinetics["old"].id, status=RecordReviewStatus.rejected, note="rejected")
    set_review(db_session, record_type=SubmissionRecordType.kinetics, record_id=kinetics["rerun"].id, status=RecordReviewStatus.approved)

    out = snapshot_cases.caught_error(db_session)
    deposit = next(d for d in out["rejected_kinetics"] if d["rejected_kinetics_ref"] == kinetics["old"].public_ref)
    by_reaction = {s["reaction"]: s for s in deposit["selection_by_reaction_entry"]}
    assert set(by_reaction) == {"CC(C)C=O + [H] <=> CC(C)=C[O] + [H][H]", "CC(C)C=O + [H] <=> C[C](C)C=O + [H][H]"}
    old = by_reaction["CC(C)C=O + [H] <=> CC(C)=C[O] + [H][H]"]
    assert old["rejected_record_is_a_candidate"] is False
    assert [e["review_status"] for e in old["excluded_by_review"]] == ["rejected"]
    assert old["outcome"] == "no_applicable_candidate"
    rerun = by_reaction["CC(C)C=O + [H] <=> C[C](C)C=O + [H][H]"]
    assert [c["kinetics_ref"] for c in rerun["candidates"]] == [kinetics["rerun"].public_ref]
    assert _ids_free(out)


def test_species_thermo_sources_lists_records_by_content_with_nasa_values(db_session):
    species = make_species(db_session, smiles="[OH]", multiplicity=2)
    entry = make_species_entry(db_session, species=species)
    tools = [make_workflow_tool_release(db_session, name=name) for name in ("zz-model", "aa-workflow")]
    for tool in tools:
        thermo = make_thermo_scalar(db_session, species_entry=entry, workflow_tool_release_id=tool.id, h298_kj_mol=None, s298_j_mol_k=None)
        thermo.phase = PhaseKind.gas
        thermo.reference_pressure_bar = 1.0
        attach_thermo_nasa(db_session, thermo=thermo)
    db_session.flush()

    out = snapshot_cases.species_thermo_sources(db_session)
    row = next(r for r in out["species"] if r["species"] == "[OH]")
    assert [r["source"] for r in row["records"]] == ["aa-workflow", "zz-model"]
    for record in row["records"]:
        assert record["cp_300_j_mol_k"]["from"].startswith("NASA7")
        assert record["h298_kj_mol"]["value"] is not None
    assert _ids_free(out)


def test_ch3_records_and_a_selection_per_dlpno_energy_level(db_session):
    from tests.services.scientific_read._factories import make_lot

    species = make_species(db_session, smiles="[CH3]", multiplicity=2)
    entry = make_species_entry(db_session, species=species)
    lot = make_lot(db_session, method="dlpno-ccsd(t)-f12", basis="cc-pvtz-f12")
    for h298 in (144.8, 143.8):
        thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=h298)
        thermo.energy_level_of_theory_id = lot.id
    db_session.flush()

    out = snapshot_cases.ch3_enthalpy_of_formation(db_session)
    assert [r["h298_kj_mol"] for r in out["records"]] == [143.8, 144.8]
    (selection,) = out["selections"]
    assert selection["with_level_filter"]["filters"] == {"level_of_theory_ref": lot.public_ref}
    assert selection["with_level_filter"]["outcome"]
    assert _ids_free(out)


def _published_release(db_session):
    """A published release selecting one of two approved, attested thermo records of one species entry."""
    curator = AppUser(username="snapshot-cases-curator", role=AppUserRole.curator)
    db_session.add(curator)
    db_session.flush()
    entry = make_species_entry(db_session, species=make_species(db_session, smiles="CCO"))
    candidates = [
        make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=h298) for h298 in (-234.5, -235.9)
    ]
    for row in candidates:
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=row.id,
            status=RecordReviewStatus.approved,
            actor=curator,
            note="approved",
        )
    deposit_and_attest(
        db_session, depositor=curator, records=[(SubmissionRecordType.thermo, row.id) for row in candidates]
    )
    policy = resolve_curation_policy(
        db_session, name="snapshot-cases", version="1", description="test", criteria={}, created_by=curator.id
    )
    release = create_release(
        db_session,
        tag="snapshot-cases-1",
        title="test release",
        curation_policy=policy,
        data_license="CC-BY-4.0",
        code_license="MIT",
        citation_text="test citation",
        contact="test@example.org",
        changelog_entry="first",
        created_by=curator.id,
    )
    add_selection(
        db_session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=candidates[0].id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=entry.id,
        rationale="the first candidate",
        selected_by=curator.id,
    )
    publish_release(db_session, release)
    freeze_manifest(db_session, release, created_by=curator.id)
    return release, candidates


def test_dataset_release_lists_selections_candidates_and_a_verified_manifest(db_session):
    draft_release, thermo_candidates = _published_release(db_session)

    out = snapshot_cases.dataset_release(db_session)
    release = next(r for r in out["releases"] if r["tag"] == draft_release.tag)
    assert release["has_doi"] is False
    (selection,) = release["selections"]
    assert selection["record"]["h298_kj_mol"] == thermo_candidates[0].h298_kj_mol
    assert release["candidates_by_type"] == {"thermo": 2}
    assert [c["record"]["h298_kj_mol"] for c in release["candidates_shipped"]] == sorted(
        c.h298_kj_mol for c in thermo_candidates
    )
    manifest = release["manifest"]
    assert manifest["verified"] is True
    assert all(a["sha256_matches_stored_content"] for a in manifest["artifacts"])
    assert "sha256" not in json.dumps(manifest["artifacts"]).replace("sha256_matches", "")
    assert _ids_free(out)


def test_hessian_summary_counts_calculations_without_a_hessian_by_reason_and_owner(db_session):
    species = make_species(db_session, smiles="O")
    entry = make_species_entry(db_session, species=species)
    before = snapshot_generators.hessian_reanalysis(db_session)["summary"]
    freq = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_freq_result(db_session, calculation=freq, frequencies_cm1=[1595.0, 3657.0, 3756.0])
    make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)

    after = snapshot_generators.hessian_reanalysis(db_session)["summary"]
    reasons = after["without_a_hessian_by_reason"]
    previous = before["without_a_hessian_by_reason"]
    no_hessian = "frequency list stored, Hessian not stored"
    sp = "sp calculation: not a frequency calculation"
    assert reasons[no_hessian]["minima"] == previous.get(no_hessian, {"minima": 0})["minima"] + 1
    assert reasons[sp]["minima"] == previous.get(sp, {"minima": 0})["minima"] + 1
    assert after["calculations_without_a_hessian"] == before["calculations_without_a_hessian"] + 2
    assert set(after["stored_hessians"]) == {"minima", "transition_states", "other"}


def test_transition_state_evidence_orders_entries_by_reaction_not_by_ref(db_session):
    for smiles in ("CCC", "C", "CC"):
        entry, _, _ = _reaction(db_session, [(smiles, 1), ("[H]", 2)], [(smiles + "O", 1)])
        make_transition_state_entry(db_session, transition_state=make_transition_state(db_session, reaction_entry=entry))
    out = generators.transition_state_evidence(db_session)
    reactions = [r["reaction"] for r in out["entries"]]
    assert reactions == sorted(reactions)
    assert _ids_free(out)


def test_candidate_lineage_emits_no_submission_timestamp(db_session):
    species = make_species(db_session, smiles="CCO")
    entry = make_species_entry(db_session, species=species)
    for h298 in (-200.0, -210.0):
        make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=h298)
    out = generators.candidate_lineage(db_session)
    row = next(r for r in out["lineage"] if r["species_entry_ref"] == entry.public_ref)
    assert [c["h298_kj_mol"] for c in row["candidates"]] == [-210.0, -200.0]
    assert "submitted_at" not in json.dumps(out)
