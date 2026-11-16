"""The CHEMKIN export of the snapshot use case: the generator's own logic and the build step's checks.

Cantera is part of the backend CI environment, so one test loads a mechanism with the real strict ``ck2yaml`` and
checks k(300 K) and H298 as Cantera evaluates them. The other tests fake the loader where they test the generator's
own logic, so that logic is also checked on its own. The build refuses a snapshot whose output does not show Cantera
having loaded the files and agreeing with the records.
"""

from __future__ import annotations

import io
import json
import math
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import cantera as ct
import pytest

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, ArrheniusAUnits, PhaseKind, RecordReviewStatus, SubmissionRecordType
from app.services.deposit.expected_outputs import render_json
from app.services.record_review import set_record_review_status
from app.services.release.curation import add_selection, create_release, publish_release, resolve_curation_policy
from scripts.paper import chemkin_export
from scripts.paper.registry import GENERATORS
from tests.scripts.test_snapshot_generators import _no_integer_ids
from tests.services.release._attest import deposit_and_attest
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
)

SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "scripts" / "paper_snapshot"


def _ref(prefix: str, letter: str) -> str:
    return f"{prefix}_{letter * 26}"


CHEM_INP = """ELEMENTS
C H O
END

SPECIES
H1O1   ! SMILES=[OH]  MULT=2 ref={oh}
H2O1   ! SMILES=O  MULT=1 ref={h2o}
C1H4   ! SMILES=C  MULT=1 ref={ch4}
C1H3   ! SMILES=[CH3]  MULT=2 ref={ch3}
END

REACTIONS CAL/MOLE MOLES
C1H4 + H1O1 <=> C1H3 + H2O1   1.4598E+03 3.468 2033.0521
END
""".format(oh=_ref("spc", "a"), h2o=_ref("spc", "b"), ch4=_ref("spc", "c"), ch3=_ref("spc", "d"))

THERM_DAT = """THERMO ALL
   300.000  1000.000  5000.000
! C1H3  SMILES=[CH3]  MULT=2  ref={ch3}
C1H3                    C   1H   3          G    10.000  3000.000  646.79      1
 3.24887000E+00 5.01881000E-03-1.26761000E-06 3.21894000E-11 2.01583000E-14    2
 1.62410000E+04 3.46319000E+00 3.99122000E+00 4.27522000E-04 9.38093000E-06    3
-1.09443000E-08 4.26310000E-12 1.61450000E+04 2.05281000E-01                   4
END
""".format(ch3=_ref("spc", "d"))

#: The coefficients above, evaluated by hand from the NASA-7 formula at 298.15 K (below the 646.79 K midpoint).
CH3_H298_KJ_MOL = 144.815


def _manifest(stamp: str, ref: str, gaps: list | None = None) -> dict:
    return {
        "format": {"name": "tckdb.export", "version": 2},
        "generated_at": stamp,
        "seed": {"reaction_refs": [ref]},
        "counts": {"gaps": len(gaps or []), "reactions": 1, "species": 4},
        "gaps": gaps or [],
        "profile": "exploratory",
        "min_review_status": "approved",
        "transport_file": {"written": False, "reason_code": "no_exported_species_has_transport", "reason": "none"},
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_chem_inp_species_and_the_written_rate_are_read():
    parsed = chemkin_export.parse_chem_inp(CHEM_INP)
    assert [(s["name"], s["smiles"], s["multiplicity"]) for s in parsed["species"]] == [
        ("H1O1", "[OH]", 2),
        ("H2O1", "O", 1),
        ("C1H4", "C", 1),
        ("C1H3", "[CH3]", 2),
    ]
    assert parsed["species"][3]["species_ref"] == _ref("spc", "d")
    assert parsed["reaction_units"] == ["CAL/MOLE", "MOLES"]
    assert parsed["reactions"] == [
        {
            "equation": "C1H4 + H1O1 <=> C1H3 + H2O1",
            "a": 1459.8,
            "n": 3.468,
            "ea": 2033.0521,
            "a_text": "1.4598E+03",
            "n_text": "3.468",
            "ea_text": "2033.0521",
        }
    ]


def test_a_species_line_without_its_comment_is_refused_not_skipped():
    with pytest.raises(ValueError, match="unreadable species line"):
        chemkin_export.parse_chem_inp("SPECIES\nC1H4\nEND\n")


def test_therm_dat_polynomial_is_read_and_h298_matches_the_formula():
    record = chemkin_export.parse_therm_dat(THERM_DAT)["C1H3"]
    assert (record["t_low_k"], record["t_mid_k"], record["t_high_k"]) == (10.0, 646.79, 3000.0)
    assert record["low"][0] == 3.99122 and record["high"][0] == 3.24887 and len(record["low"]) == len(record["high"]) == 7
    assert math.isclose(chemkin_export.nasa7_h_kj_mol(record, 298.15), CH3_H298_KJ_MOL, abs_tol=1e-3)


def test_nasa7_h_is_exact_for_a_constant_cp_polynomial():
    record = {"t_low_k": 100.0, "t_mid_k": 1000.0, "t_high_k": 5000.0, "low": [2.5, 0, 0, 0, 0, 0, 0], "high": [3.5, 0, 0, 0, 0, 0, 0]}
    gas_constant = 8.314462618
    assert math.isclose(chemkin_export.nasa7_h_kj_mol(record, 300.0), 2.5 * gas_constant * 300.0 / 1000.0, rel_tol=1e-9)
    assert math.isclose(chemkin_export.nasa7_h_kj_mol(record, 2000.0), 3.5 * gas_constant * 2000.0 / 1000.0, rel_tol=1e-9)


def test_nasa7_h_outside_the_polynomial_range_is_refused():
    record = chemkin_export.parse_therm_dat(THERM_DAT)["C1H3"]
    with pytest.raises(ValueError, match="outside"):
        chemkin_export.nasa7_h_kj_mol(record, 5.0)


def test_the_file_digest_ignores_refs_and_the_manifest_timestamp_and_nothing_else():
    other_chem = CHEM_INP.replace(_ref("spc", "d"), _ref("spc", "e"))
    assert chemkin_export.masked_sha256("chem.inp", CHEM_INP.encode()) == chemkin_export.masked_sha256("chem.inp", other_chem.encode())
    changed_rate = CHEM_INP.replace("3.468", "3.469")
    assert chemkin_export.masked_sha256("chem.inp", CHEM_INP.encode()) != chemkin_export.masked_sha256("chem.inp", changed_rate.encode())

    def manifest(stamp, ref, gaps=None):
        return json.dumps(_manifest(stamp, ref, gaps)).encode()

    base = chemkin_export.masked_sha256("manifest.json", manifest("2026-01-01T00:00:00+00:00", _ref("rxe", "a")))
    assert base == chemkin_export.masked_sha256("manifest.json", manifest("2027-02-02T10:00:00+00:00", _ref("rxe", "b")))
    assert base != chemkin_export.masked_sha256("manifest.json", manifest("2026-01-01T00:00:00+00:00", _ref("rxe", "a"), [{"code": "x"}]))


def test_ratio_is_reported_as_it_is_to_six_figures():
    assert chemkin_export.ratio(1.87889e10, 1.87469e10) == 1.00224
    assert chemkin_export.ratio(3.0, 2.0) == 1.5
    assert chemkin_export.ratio(None, 2.0) is None
    assert chemkin_export.ratio(2.0, None) is None
    assert chemkin_export.ratio(2.0, 0.0) is None


def test_the_manifests_gaps_are_kept_and_ordered_by_content():
    gaps = [{"code": "b_gap", "subject": "z"}, {"code": "a_gap", "subject": "y"}]
    summary = chemkin_export.manifest_summary(_manifest("t", _ref("rxe", "a"), gaps))
    assert [g["code"] for g in summary["gaps"]] == ["a_gap", "b_gap"]
    assert summary["transport_file"]["reason_code"] == "no_exported_species_has_transport"
    assert summary["counts"]["gaps"] == 2


def test_a_missing_cantera_is_a_status_not_a_pass(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "cantera", None)
    result = chemkin_export.cantera_load(tmp_path / "chem.inp", tmp_path / "therm.dat", None, 300.0)
    assert result["status"] == "cantera_not_installed"


# ---------------------------------------------------------------------------
# the generator, on seeded rows
# ---------------------------------------------------------------------------


def _fake_cantera(k_cm3_mol_s: float):
    def load(chem, therm, tran, temperature_k):
        return {
            "status": "loaded",
            "version": "test",
            "converter": "test",
            "species": ["C1H3", "C1H4", "H1O1", "H2O1"],
            "reactions": [
                {
                    "equation": "C1H4 + H1O1 <=> C1H3 + H2O1",
                    "reactants": ["C1H4", "H1O1"],
                    "products": ["C1H3", "H2O1"],
                    "order": 2,
                    "k_forward_cm3_mol_s": k_cm3_mol_s,
                }
            ],
            "h298_kj_mol": {"C1H3": 144.815},
        }

    return load


SMILES = (("C", 1), ("[OH]", 2), ("O", 1), ("[CH3]", 2))


def _seed(db_session):
    species = {s: make_species(db_session, smiles=s, multiplicity=m) for s, m in SMILES}
    entries = {s: make_species_entry(db_session, species=sp) for s, sp in species.items()}
    reaction = make_chem_reaction(db_session, reactants=[species["C"], species["[OH]"]], products=[species["O"], species["[CH3]"]])
    entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[entries["C"], entries["[OH]"]],
        product_entries=[entries["O"], entries["[CH3]"]],
    )
    kinetics = make_kinetics(
        db_session, reaction_entry=entry, a=1459.78, a_units=ArrheniusAUnits.cm3_mol_s, n=3.46761, ea_kj_mol=8.50629
    )
    thermo = {s: make_thermo_scalar(db_session, species_entry=e, h298_kj_mol=144.81522 if s == "[CH3]" else -10.0) for s, e in entries.items()}
    return entry, entries, kinetics, thermo


def _make_exportable(db_session, thermo) -> None:
    """Give each thermo row a gas-phase, 1 atm NASA-7 polynomial, which is what the exporter writes."""
    for record in thermo.values():
        attach_thermo_nasa(db_session, thermo=record)
        record.phase = PhaseKind.gas
        record.reference_pressure_bar = 1.01325
    db_session.flush()


def _files(entry, kinetics, thermo, stamp="2026-01-01T00:00:00+00:00") -> tuple[dict, dict[str, bytes]]:
    request = {
        "seed": {"reaction_refs": [entry.public_ref]},
        "kinetics_refs": [kinetics.public_ref],
        "thermo_refs": sorted(t.public_ref for t in thermo.values()),
        "energy_units": "cal/mol",
        "naming_policy": "formula",
    }
    files = {
        "chem.inp": CHEM_INP.encode(),
        "therm.dat": THERM_DAT.encode(),
        "manifest.json": json.dumps(_manifest(stamp, entry.public_ref)).encode(),
    }
    return request, files


def test_the_generator_is_registered():
    assert GENERATORS["use_case_chemkin_export"] is chemkin_export.use_case_chemkin_export


def test_without_the_release_the_generator_says_unresolved_and_checks_nothing(db_session):
    out = chemkin_export.use_case_chemkin_export(db_session)
    assert out["export"] == "unresolved" and "snapshot-demo-1" in out["reason"]


def test_the_analysis_names_species_hashes_files_and_compares_rate_and_h298(db_session, monkeypatch):
    entry, _, kinetics, thermo = _seed(db_session)
    request, files = _files(entry, kinetics, thermo)
    stored_k = 1459.78 * 300.0**3.46761 * math.exp(-8506.29 / (8.314462618 * 300.0))
    monkeypatch.setattr(chemkin_export, "cantera_load", _fake_cantera(stored_k * 1.0022))

    out = chemkin_export.analyse(db_session, request, files)

    assert out["export"] == "found"
    assert [r["smiles"] for r in out["species"]] == ["C", "O", "[CH3]", "[OH]"]
    for row in out["species"]:
        assert row["thermo_refs_named"] == [thermo[row["smiles"]].public_ref]
        assert row["has_nasa_in_therm_dat"] is (row["smiles"] == "[CH3]")
    assert out["named_thermo_refs_without_a_species_in_chem_inp"] == []
    assert [f["file"] for f in out["files"]] == ["chem.inp", "therm.dat", "manifest.json"]
    assert out["manifest"]["gaps"] == [] and out["manifest"]["transport_file"]["written"] is False
    rate = out["forward_rate"]
    assert math.isclose(rate["stored_fit_k"], stored_k, rel_tol=1e-5)
    assert rate["cantera_over_stored"] == pytest.approx(1.0022, abs=1e-5)
    assert rate["written_in_chem_inp"]["n"] == 3.468 and rate["stored_fit"]["n"] == 3.46761
    h = out["ch3_h298"]
    assert h["record_h298_kj_mol"] == pytest.approx(144.815, abs=1e-3)
    assert h["exported_nasa_h298_kj_mol"] == pytest.approx(CH3_H298_KJ_MOL, abs=1e-3)
    assert abs(h["exported_minus_record_kj_mol"]) < 1e-3
    assert not _no_integer_ids(json.loads(render_json(out)))


def test_exports_that_differ_only_in_the_timestamp_give_the_same_analysis(db_session, monkeypatch):
    entry, _, kinetics, thermo = _seed(db_session)
    monkeypatch.setattr(chemkin_export, "cantera_load", _fake_cantera(1.0e10))
    request, one = _files(entry, kinetics, thermo, stamp="2026-01-01T00:00:00+00:00")
    _, two = _files(entry, kinetics, thermo, stamp="2027-05-05T05:05:05+00:00")
    assert chemkin_export.analyse(db_session, request, one) == chemkin_export.analyse(db_session, request, two)


def test_a_named_record_whose_species_is_not_in_chem_inp_is_reported(db_session, monkeypatch):
    entry, _, kinetics, thermo = _seed(db_session)
    stray = make_thermo_scalar(
        db_session, species_entry=make_species_entry(db_session, species=make_species(db_session, smiles="N", multiplicity=1))
    )
    request, files = _files(entry, kinetics, {**thermo, "N": stray})
    monkeypatch.setattr(chemkin_export, "cantera_load", _fake_cantera(1.0e10))
    assert chemkin_export.analyse(db_session, request, files)["named_thermo_refs_without_a_species_in_chem_inp"] == ["N"]


def test_saved_files_equal_the_in_process_export_apart_from_the_timestamp():
    same = chemkin_export.unchanged_by_timestamp
    assert same("chem.inp", b"a", b"a") and not same("chem.inp", b"a", b"b")
    one = json.dumps(_manifest("2026-01-01T00:00:00+00:00", _ref("rxe", "a"))).encode()
    two = json.dumps(_manifest("2027-01-01T00:00:00+00:00", _ref("rxe", "a"))).encode()
    other = json.dumps(_manifest("2027-01-01T00:00:00+00:00", _ref("rxe", "b"))).encode()
    assert same("manifest.json", one, two) and not same("manifest.json", one, other)


def _release_with_the_use_case(db_session, entries, kinetics, thermo, decoys, *, publish=True, selected_ch3=None):
    """A published release ``snapshot-demo-1`` selecting the rate record and the [CH3] thermo record; every record
    approved and deposited together by one submission, except ``decoys`` (another deposit's records)."""
    curator = AppUser(username="chemkin-export-curator", role=AppUserRole.curator)
    db_session.add(curator)
    db_session.flush()
    records = [(SubmissionRecordType.kinetics, kinetics.id)] + [(SubmissionRecordType.thermo, t.id) for t in thermo.values()]
    for record_type, record_id in records + [(SubmissionRecordType.thermo, d.id) for d in decoys]:
        set_record_review_status(
            db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.approved, actor=curator, note="ok"
        )
    deposit_and_attest(db_session, depositor=curator, records=records)
    deposit_and_attest(db_session, depositor=curator, records=[(SubmissionRecordType.thermo, d.id) for d in decoys])
    policy = resolve_curation_policy(
        db_session, name="chemkin-export", version="1", description="test", criteria={}, created_by=curator.id
    )
    release = create_release(
        db_session,
        tag=chemkin_export.RELEASE_TAG,
        title="t",
        curation_policy=policy,
        data_license="CC-BY-4.0",
        code_license="MIT",
        citation_text="c",
        contact="a@example.org",
        changelog_entry="first",
        created_by=curator.id,
    )
    for record_type, record_id, subject_type, subject_id in (
        (SubmissionRecordType.kinetics, kinetics.id, SubmissionRecordType.reaction_entry, kinetics.reaction_entry_id),
        (SubmissionRecordType.thermo, (selected_ch3 or thermo["[CH3]"]).id, SubmissionRecordType.species_entry, entries["[CH3]"].id),
    ):
        add_selection(
            db_session, release=release, record_type=record_type, record_id=record_id,
            subject_type=subject_type, subject_id=subject_id, rationale="r", selected_by=curator.id,
        )
    if publish:
        publish_release(db_session, release)


def test_the_request_is_found_from_the_release_and_the_rate_records_submission(db_session):
    entry, entries, kinetics, thermo = _seed(db_session)
    decoys = [make_thermo_scalar(db_session, species_entry=entries[s], h298_kj_mol=1.0) for s in ("C", "[CH3]")]
    _release_with_the_use_case(db_session, entries, kinetics, thermo, decoys)

    request, reason = chemkin_export.resolve_request(db_session)

    assert reason is None
    assert request["seed"] == {"reaction_refs": [entry.public_ref]}
    assert request["kinetics_refs"] == [kinetics.public_ref]
    # the decoy C and [CH3] records belong to another deposit and are not named
    assert request["thermo_refs"] == sorted(t.public_ref for t in thermo.values())


def test_the_in_process_export_returns_the_routes_files_for_the_request(db_session):
    entry, entries, kinetics, thermo = _seed(db_session)
    _make_exportable(db_session, thermo)
    _release_with_the_use_case(db_session, entries, kinetics, thermo, [])
    request, _ = chemkin_export.resolve_request(db_session)

    files = chemkin_export.export_in_process(db_session, request)

    assert {"chem.inp", "therm.dat", "manifest.json"} <= set(files)
    assert all(ref in files["chem.inp"].decode() for ref in (entries[s].species.public_ref for s in ("C", "[CH3]")))
    parsed = chemkin_export.parse_chem_inp(files["chem.inp"].decode())
    assert sorted(s["smiles"] for s in parsed["species"]) == ["C", "O", "[CH3]", "[OH]"]
    assert len(parsed["reactions"]) == 1
    names = {s["name"] for s in parsed["species"]}
    assert set(chemkin_export.parse_therm_dat(files["therm.dat"].decode())) == names
    assert json.loads(files["manifest.json"])["gaps"] == []
    # The exporter's own output goes through the generator's analysis (Cantera's loader faked: the files are the point).
    out = chemkin_export.analyse(db_session, request, files)
    assert out["export"] == "found" and out["named_thermo_refs_without_a_species_in_chem_inp"] == []
    assert all(row["has_nasa_in_therm_dat"] and len(row["thermo_refs_named"]) == 1 for row in out["species"])


# ---------------------------------------------------------------------------
# the build step
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def step():
    sys.path.insert(0, str(SNAPSHOT_DIR))
    try:
        import use_case_export as module

        yield module
    finally:
        sys.path.remove(str(SNAPSHOT_DIR))


def _config(runs=("run/a",), plan=None, tag=None, selections=None):
    tag = tag or chemkin_export.RELEASE_TAG
    if selections is None:
        selections = [
            {"run": "run/a", "record": "kinetics"},
            {"run": "run/a", "record": "thermo", "smiles": "[CH3]"},
        ]
    return {
        "sources": {"arc_runs": {"runs": list(runs)}},
        "chemkin_export": plan,
        "curation": {"release": {"tag": tag, "selections": selections}},
    }


SPECIES = list(chemkin_export.NAMED_SPECIES)


def test_the_plan_must_name_a_deposited_run_and_distinct_species(step):
    assert step.problems(_config(plan=None)) == []
    assert step.problems(_config(plan={"run": "run/a", "species": SPECIES})) == []
    assert any("not in sources.arc_runs.runs" in p for p in step.problems(_config(plan={"run": "run/z", "species": SPECIES})))
    assert any("species" in p for p in step.problems(_config(plan={"run": "run/a", "species": ["C", "C"]})))
    assert any("species" in p for p in step.problems(_config(plan={"run": "run/a", "species": []})))
    assert any("unknown key" in p for p in step.problems(_config(plan={"run": "run/a", "species": SPECIES, "pick": 1})))


def test_the_plan_must_agree_with_the_release_and_species_the_generator_reads(step):
    plan = {"run": "run/a", "species": SPECIES}
    assert any("species must be" in p for p in step.problems(_config(plan={"run": "run/a", "species": ["C", "[OH]", "O"]})))
    assert any("release.tag" in p for p in step.problems(_config(plan=plan, tag="other-release")))
    assert any("lacks kinetics" in p for p in step.problems(_config(plan=plan, selections=[{"run": "run/a", "record": "thermo", "smiles": "[CH3]"}])))
    assert any("lacks thermo" in p for p in step.problems(_config(plan=plan, selections=[{"run": "run/a", "record": "kinetics"}])))


class _Curator:
    """Answers the three calls the request builder makes from a table of refs."""

    def __init__(self, thermo_by_smiles, extra_search=()):
        self.thermo_by_smiles = thermo_by_smiles
        self.extra = list(extra_search)

    def ok(self, method, path, **kwargs):
        kind, _, number = path.removeprefix("/record-reviews/").partition("/")
        if path.startswith("/record-reviews/"):
            return {"record_public_ref": f"{kind}-{number}"}
        smiles = kwargs["params"]["smiles"]
        refs = self.thermo_by_smiles.get(smiles, []) + self.extra
        return {"records": [{"thermo": {"thermo_ref": r}} for r in refs]}


def _report_with_run(step, run, **response):
    sys.path.insert(0, str(SNAPSHOT_DIR))
    try:
        from common import Report
    finally:
        sys.path.remove(str(SNAPSHOT_DIR))
    report = Report()
    report.source("arc_runs")["runs"] = [
        {"run": run, "payloads": [{"status": "uploaded", "payload_kind": "computed_reaction", "response": response}]}
    ]
    return report


RESPONSE = {"reaction_entry_id": 7, "kinetics_ids": [9], "thermo_ids": [1, 2]}


def test_the_request_names_the_runs_reaction_rate_and_one_thermo_record_per_species(step):
    curator = _Curator({"C": ["thermo-1", "thermo-5"], "[OH]": ["thermo-2"]})
    report = _report_with_run(step, "run/a", **RESPONSE)
    body = step.build_request(curator, report, {"run": "run/a", "species": ["C", "[OH]"]})
    assert body["seed"] == {"reaction_refs": ["reaction_entry-7"]}
    assert body["kinetics_refs"] == ["kinetics-9"]
    # thermo-5 is a record of another deposit for the same SMILES: it is not named.
    assert body["thermo_refs"] == ["thermo-1", "thermo-2"]


def test_a_species_the_run_has_no_record_for_stops_the_build(step):
    report = _report_with_run(step, "run/a", **RESPONSE)
    with pytest.raises(RuntimeError, match="0 thermo records for O"):
        step.build_request(_Curator({"O": ["thermo-99"]}), report, {"run": "run/a", "species": ["O"]})


def test_the_release_selection_must_be_named_in_the_request(step):
    request = {"kinetics_refs": ["kin_x"], "thermo_refs": ["thm_a", "thm_b"]}
    release = {"selections": [{"record": "kinetics", "record_ref": "kin_x"}, {"record": "thermo", "record_ref": "thm_b"},
                              {"record": "transition_state_entry", "record_ref": "tse_q"}]}
    assert step.check_against_release(request, {}, release) == ["kinetics kin_x", "thermo thm_b"]
    release["selections"].append({"record": "thermo", "record_ref": "thm_other"})
    with pytest.raises(RuntimeError, match="does not name"):
        step.check_against_release(request, {}, release)


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_the_files_are_written_with_their_digests_and_other_members_are_refused(step, tmp_path):
    members = {"chem.inp": b"a", "therm.dat": b"b", "manifest.json": b"{}"}
    written = step.write_files(_zip(members), tmp_path / "out")
    assert sorted(written) == ["chem.inp", "manifest.json", "therm.dat"]
    assert written["chem.inp"]["bytes"] == 1 and (tmp_path / "out" / "therm.dat").read_bytes() == b"b"
    with pytest.raises(RuntimeError, match="unexpected member"):
        step.write_files(_zip({**members, "../evil": b"x"}), tmp_path / "out2")
    with pytest.raises(RuntimeError, match="lacks"):
        step.write_files(_zip({"chem.inp": b"a"}), tmp_path / "out3")


def _good_output() -> dict:
    return {
        "export": "found",
        "cantera": {"status": "loaded"},
        "manifest": {"gaps": []},
        "forward_rate": {
            "cantera_k_forward": 1.87889e10,
            "stored_fit_k": 1.87469e10,
            "stored_fit_units": "cm3_mol_s",
            "units": "cm3_mol_s",
            "cantera_over_stored": 1.00000001,
            "ratio_explained_by_written_digits": {"total": 1.0e-7},
            "written_in_chem_inp": {"n_text": "3.46761000E+00", "a_text": "1.45978000E+03", "ea_text": "2.03305213E+03"},
            "written_vs_stored_fit": {
                "n": {"over_half_unit": 0.78, "over_half_unit_unrounded": 0.78},
                "a": {"over_half_unit": 0.4, "over_half_unit_unrounded": 0.4},
                "ea": {"over_half_unit": 0.9, "over_half_unit_unrounded": 0.9},
            },
            "cantera_over_hand_evaluation_of_written_digits": 1.0,
        },
        "ch3_h298": {
            "exported_nasa_h298_kj_mol": 144.815,
            "record_h298_kj_mol": 144.815,
            "exported_minus_record_kj_mol": -0.00022,
            "cantera_minus_exported_kj_mol": 0.0,
            "cantera_agreement_bound_kj_mol": 0.00014,
        },
        "species": [
            {
                "smiles": smiles,
                "thermo_refs_named": ["thm_" + smiles],
                "has_nasa_in_therm_dat": True,
                "nasa_vs_record": {
                    "numbers_compared": 17,
                    "max_difference_over_half_unit": 0.0,
                    "max_difference_over_half_unit_unrounded": 0.0,
                    "min_coefficient_significant_digits": 9,
                    "t_low_decimals": 3,
                    "t_mid_decimals": 2,
                    "t_high_decimals": 3,
                },
            }
            for smiles in chemkin_export.NAMED_SPECIES
        ],
        "named_thermo_refs_without_a_species_in_chem_inp": [],
    }


def _with(document: dict, **changes) -> dict:
    """A copy of ``document`` with ``changes`` applied (``section__key`` sets ``document[section][key]``)."""
    out = json.loads(json.dumps(document))
    for path, value in changes.items():
        section, _, key = path.partition("__")
        if key:
            out[section][key] = value
        else:
            out[section] = value
    return out


def _rate_with(**changes) -> dict:
    document = _good_output()
    for key, value in changes.items():
        if key in document["forward_rate"]["written_in_chem_inp"]:
            document["forward_rate"]["written_in_chem_inp"][key] = value
        else:
            document["forward_rate"][key] = value
    return document


def test_the_build_accepts_only_an_output_that_shows_the_files_loaded_and_checked(step):
    assert step.verify_output(_good_output()) == []
    assert step.verify_output({"export": "unresolved", "reason": "no release"})
    absent = _with(_good_output(), cantera={"status": "cantera_not_installed", "detail": "no module"})
    assert any("Cantera did not load" in r for r in step.verify_output(absent))
    refused = _with(_good_output(), cantera={"status": "refused", "error": "ck2yaml: bad"})
    assert any("refused" in r for r in step.verify_output(refused))
    no_rate = _with(_good_output(), forward_rate={"cantera_k_forward": None})
    assert step.verify_output(no_rate)
    no_nasa = _good_output()
    no_nasa["species"][0]["has_nasa_in_therm_dat"] = False
    assert step.verify_output(no_nasa)


def test_a_rate_ratio_the_written_digits_cannot_explain_is_refused(step):
    wrong = _with(_good_output(), forward_rate__cantera_over_stored=1000.0)
    assert any("1000.0" in r and "explain" in r for r in step.verify_output(wrong))
    assert step.verify_output(_with(_good_output(), forward_rate__cantera_over_stored=0.99))
    assert step.verify_output(_with(_good_output(), forward_rate__ratio_explained_by_written_digits=None))
    assert step.verify_output(_with(_good_output(), forward_rate__cantera_over_stored=1.0000001)) == []


def test_rates_in_other_units_are_refused(step):
    other = _with(_good_output(), forward_rate__stored_fit_units="cm3_molecule_s")
    assert any("not cm3_mol_s" in r for r in step.verify_output(other))


def test_a_coarser_file_is_refused_even_when_its_ratio_is_inside_its_own_wider_bound(step):
    # n written as 3.468: the bound computed from that file is wide enough for a ratio of 1.0022
    coarse_n = _rate_with(n_text="3.468", cantera_over_stored=1.0022)
    coarse_n["forward_rate"]["ratio_explained_by_written_digits"]["total"] = 0.0029
    reasons = step.verify_output(coarse_n)
    assert any("n is written as '3.468'" in r and "9 significant digits" in r for r in reasons)
    assert step.verify_output(_rate_with(n_text="3")), "n written to no decimals"
    assert any("n is written" in r for r in step.verify_output(_rate_with(n_text="3.4676100E+00")))
    assert any("A is written" in r for r in step.verify_output(_rate_with(a_text="1.4598E+03")))
    assert any("Ea is written" in r for r in step.verify_output(_rate_with(ea_text="2033.0521")))


def test_an_unnormalised_a_does_not_meet_the_floor_by_showing_decimals(step):
    # 0.00000000E+07 shows 8 mantissa decimals; 0.0001000000E+07 is one significant digit however many it shows
    reasons = step.verify_output(_rate_with(a_text="0.0001000000E+07"))
    assert any("A is written as '0.0001000000E+07'" in r and "significant digits" in r for r in reasons)
    assert step.verify_output(_rate_with(a_text="1.45978000E+03")) == []
    assert step.verify_output(_rate_with(a_text="1459.78000")) == []
    assert step.verify_output(_rate_with(a_text="not a number"))
    assert chemkin_export.decimal_places("3.5E-1") == 2 and chemkin_export.decimal_places("12") == 0


def test_a_coarser_nasa_temperature_is_refused(step):
    for key, coarse in (("t_mid", 1), ("t_low", 2), ("t_high", 2)):
        document = _good_output()
        document["species"][0]["nasa_vs_record"][f"{key}_decimals"] = coarse
        assert any(f"{key} is written with {coarse} decimals" in r for r in step.verify_output(document)), key
    more = _good_output()
    more["species"][0]["nasa_vs_record"].update(t_low_decimals=5, t_mid_decimals=4, t_high_decimals=5)
    assert step.verify_output(more) == []
    missing = _good_output()
    del missing["species"][0]["nasa_vs_record"]["t_mid_decimals"]
    assert step.verify_output(missing)


def test_the_species_must_be_present_and_exactly_the_ones_the_generator_names(step):
    no_key = _good_output()
    del no_key["species"]
    assert any("no species" in r for r in step.verify_output(no_key))
    assert any("no species" in r for r in step.verify_output(_with(_good_output(), species=[])))
    fewer = _good_output()
    fewer["species"] = fewer["species"][:3]
    assert any("not" in r and "species are" in r for r in step.verify_output(fewer))
    extra = _good_output()
    extra["species"].append({**extra["species"][0], "smiles": "N"})
    assert any("species are" in r for r in step.verify_output(extra))
    swapped = _good_output()
    swapped["species"][0]["smiles"] = "N"
    assert any("species are" in r for r in step.verify_output(swapped))


def test_a_file_written_with_more_digits_still_passes(step):
    finer = _rate_with(n_text="3.467610000E+00", a_text="1.459780000E+03", ea_text="2.033052130E+03")
    finer["forward_rate"]["written_vs_stored_fit"] = {k: {"over_half_unit": 0.1, "over_half_unit_unrounded": 0.1} for k in ("n", "a", "ea")}
    finer["forward_rate"]["ratio_explained_by_written_digits"]["total"] = 0.00005
    finer["forward_rate"]["cantera_over_stored"] = 1.00001
    finer["species"][0]["nasa_vs_record"]["min_coefficient_significant_digits"] = 13
    assert step.verify_output(finer) == []


def test_cancelling_errors_in_a_and_n_are_caught_parameter_by_parameter(step):
    # k(300 K) is right but n and A are each off: the 300 K ratio alone would pass
    cancelling = _good_output()
    cancelling["forward_rate"]["written_vs_stored_fit"]["n"]["over_half_unit_unrounded"] = 60.0
    cancelling["forward_rate"]["written_vs_stored_fit"]["a"]["over_half_unit_unrounded"] = 40.0
    reasons = step.verify_output(cancelling)
    assert any("written n differs" in r for r in reasons) and any("written a differs" in r for r in reasons)
    assert any("written ea differs" in r for r in step.verify_output(_with(_good_output(), forward_rate__written_vs_stored_fit={
        "n": {"over_half_unit_unrounded": 0.1}, "a": {"over_half_unit_unrounded": 0.1}, "ea": {"over_half_unit_unrounded": 3.0}})))
    assert step.verify_output(_with(_good_output(), forward_rate__written_vs_stored_fit=None))


def test_the_gate_uses_the_unrounded_distance_and_allows_only_a_float_epsilon_at_a_tie(step):
    # 1.0004 is reported as 1.0 (three figures) and must still be refused
    for distance in (1.0004, 1.000001):
        param = _good_output()
        param["forward_rate"]["written_vs_stored_fit"]["n"] = {"over_half_unit": 1.0, "over_half_unit_unrounded": distance}
        assert any("written n differs" in r for r in step.verify_output(param)), distance
        card = _good_output()
        card["species"][0]["nasa_vs_record"].update(max_difference_over_half_unit=1.0, max_difference_over_half_unit_unrounded=distance)
        assert any("half-units" in r for r in step.verify_output(card)), distance
    tie = _good_output()
    tie["forward_rate"]["written_vs_stored_fit"]["n"]["over_half_unit_unrounded"] = 1.0 + 4e-16
    tie["species"][0]["nasa_vs_record"]["max_difference_over_half_unit_unrounded"] = 1.0 + 4e-16
    assert step.verify_output(tie) == []
    assert step.HALF_UNIT_FLOAT_EPSILON == 1e-9
    missing = _good_output()
    del missing["species"][0]["nasa_vs_record"]["max_difference_over_half_unit_unrounded"]
    assert step.verify_output(missing)


def test_the_unrounded_distance_is_in_the_analysis_next_to_the_reported_one():
    written = chemkin_export.parse_chem_inp(CHEM_INP)["reactions"][0]
    out = chemkin_export.written_vs_stored_fit(written, SimpleNamespace(a=1459.78, n=3.46761, ea_kj_mol=8.50629, t0_k=1.0))
    assert out["n"]["over_half_unit"] == chemkin_export.sig(out["n"]["over_half_unit_unrounded"], 3)
    assert out["n"]["over_half_unit_unrounded"] != out["n"]["over_half_unit"]


def test_cantera_must_agree_with_the_hand_evaluation_of_the_written_digits(step):
    assert any("hand evaluation" in r for r in step.verify_output(_rate_with(cantera_over_hand_evaluation_of_written_digits=1.01)))
    assert step.verify_output(_rate_with(cantera_over_hand_evaluation_of_written_digits=None))


def test_a_nasa_card_that_is_not_the_stored_record_or_is_written_coarsely_is_refused(step):
    off = _good_output()
    off["species"][0]["nasa_vs_record"]["max_difference_over_half_unit_unrounded"] = 4.0
    assert any("half-units" in r for r in step.verify_output(off))
    coarse = _good_output()
    coarse["species"][0]["nasa_vs_record"]["min_coefficient_significant_digits"] = 6
    assert any("significant digits" in r for r in step.verify_output(coarse))
    # an unnormalised coefficient such as 0.0001E+07 is one significant digit, however many decimals it shows
    assert chemkin_export.significant_digits("0.0001E+07") == 1
    assert chemkin_export.significant_digits("3.24887000E+00") == 9
    assert chemkin_export.significant_digits("0.00000000E+00") == 9
    assert chemkin_export.significant_digits("-1.09443000E-08") == 9
    missing = _good_output()
    missing["species"][0]["nasa_vs_record"] = None
    assert any("not compared" in r for r in step.verify_output(missing))


def test_the_records_scalar_h298_is_reported_not_gated_but_cantera_must_match_the_polynomial(step):
    far = _with(_good_output(), ch3_h298__exported_minus_record_kj_mol=1.0)
    assert step.verify_output(far) == []
    cantera = _with(_good_output(), ch3_h298__cantera_minus_exported_kj_mol=1.0)
    assert any("Cantera's H298" in r for r in step.verify_output(cantera))
    assert step.verify_output(_with(_good_output(), ch3_h298__exported_nasa_h298_kj_mol=None))


def test_a_manifest_with_gaps_is_refused(step):
    gapped = _with(_good_output(), manifest={"gaps": [{"kind": "thermo_nasa", "ref": "thm_a", "detail": "no NASA"}]})
    assert any("gaps" in r for r in step.verify_output(gapped))


# ---------------------------------------------------------------------------
# compare.py
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def compare():
    sys.path.insert(0, str(SNAPSHOT_DIR))
    try:
        import compare as module

        yield module
    finally:
        sys.path.remove(str(SNAPSHOT_DIR))


def _build(root: Path, species_ref: str, stamp: str, chem_rate: str = "3.468") -> Path:
    directory = root / "chemkin_export"
    directory.mkdir(parents=True)
    (directory / "chem.inp").write_text(CHEM_INP.replace(_ref("spc", "d"), species_ref).replace("3.468", chem_rate))
    (directory / "manifest.json").write_text(json.dumps(_manifest(stamp, species_ref.replace("spc", "rxe"))))
    return root


def test_compare_masks_refs_and_the_manifest_timestamp_but_not_a_changed_value(compare, tmp_path):
    one = _build(tmp_path / "one", _ref("spc", "x"), "2026-01-01T00:00:00.1+00:00")
    two = _build(tmp_path / "two", _ref("spc", "y"), "2026-02-02T00:00:00.2+00:00")
    assert compare.compare_chemkin_export(one, two) == {
        "chem.inp": "identical after masking public refs and timestamps",
        "manifest.json": "identical after masking public refs and timestamps",
    }
    three = _build(tmp_path / "three", _ref("spc", "x"), "2026-01-01T00:00:00.1+00:00", chem_rate="3.469")
    assert compare.compare_chemkin_export(one, three)["chem.inp"] == "DIFFERS"
    (three / "chemkin_export" / "manifest.json").unlink()
    assert compare.compare_chemkin_export(one, three)["manifest.json"].startswith("DIFFERS")


def test_compare_says_when_neither_build_has_an_export_and_refuses_a_found_export_without_files(compare, tmp_path):
    for name in ("one", "two"):
        (tmp_path / name / "expected_outputs").mkdir(parents=True)
    assert compare.compare_chemkin_export(tmp_path / "one", tmp_path / "two") == {"(directory)": "absent in both builds"}
    (tmp_path / "one" / "expected_outputs" / "use_case_chemkin_export.json").write_text(json.dumps({"export": "found"}))
    result = compare.compare_chemkin_export(tmp_path / "one", tmp_path / "two")
    assert result["(build one)"].startswith("DIFFERS")
    (tmp_path / "two" / "expected_outputs" / "use_case_chemkin_export.json").write_text(json.dumps({"export": "unresolved"}))
    assert compare.compare_chemkin_export(tmp_path / "one", tmp_path / "two")["(build one)"].startswith("DIFFERS")


# ---------------------------------------------------------------------------
# bounds from the written digits
# ---------------------------------------------------------------------------


def test_half_a_unit_in_the_last_written_place():
    half = chemkin_export.half_unit_in_last_place
    assert half("3.468") == pytest.approx(5e-4)
    assert half("1.4598E+03") == pytest.approx(0.5e-4 * 1e3)
    assert half("2033.0521") == pytest.approx(5e-5)
    assert half("12") == pytest.approx(0.5)


def test_the_rate_bound_is_the_sum_of_what_each_written_number_can_move():
    written = chemkin_export.parse_chem_inp(CHEM_INP)["reactions"][0]
    bound = chemkin_export.rate_ratio_bound(written, ["CAL/MOLE", "MOLES"], 300.0)
    assert bound["n_rounding"] == pytest.approx(5e-4 * math.log(300.0), rel=1e-2)
    assert bound["a_rounding"] == pytest.approx(0.5e-1 / 1459.8, rel=1e-2)
    assert bound["ea_rounding"] == pytest.approx(5e-5 / (1.987204258 * 300.0), rel=1e-2)
    assert bound["total"] == pytest.approx(sum(v for k, v in bound.items() if k != "total"), rel=1e-2)
    # the 3.468 written for 3.46761 is inside it: that is what makes the observed ratio explicable
    observed = (1459.8 * 300.0**3.468) / (1459.78 * 300.0**3.46761)
    assert abs(math.log(observed)) < bound["total"]
    assert chemkin_export.rate_ratio_bound(written, ["KCAL/MOLE"], 300.0) is None
    assert chemkin_export.rate_ratio_bound(None, ["CAL/MOLE"], 300.0) is None


def test_the_written_reaction_is_matched_whatever_order_its_terms_are_written_in():
    sides = chemkin_export.equation_sides
    assert sides("C1H4 + H1O1 <=> H2O1 + C1H3") == sides("H1O1 + C1H4 <=> C1H3 + H2O1") == (["C1H4", "H1O1"], ["C1H3", "H2O1"])
    assert sides("A => B") == (["A"], ["B"])


def test_hand_rate_is_the_formula_on_the_written_digits():
    written = chemkin_export.parse_chem_inp(CHEM_INP)["reactions"][0]
    r = chemkin_export.GAS_CONSTANT_CAL_MOL_K
    assert chemkin_export.hand_rate(written, 300.0) == pytest.approx(1459.8 * 300.0**3.468 * math.exp(-2033.0521 / (r * 300.0)))


def test_each_written_parameter_is_compared_with_the_stored_fit_in_half_units():
    written = chemkin_export.parse_chem_inp(CHEM_INP)["reactions"][0]
    kinetics = SimpleNamespace(a=1459.78, n=3.46761, ea_kj_mol=8.50629, t0_k=1.0)
    out = chemkin_export.written_vs_stored_fit(written, kinetics)
    assert out["n"]["over_half_unit"] == pytest.approx(0.78, abs=0.01)  # 3.468 for 3.46761
    assert out["a"]["over_half_unit"] < 1 and out["ea"]["over_half_unit"] < 1
    # a different reference temperature changes the A the file must hold: k = A (T/T0)^n is written as A T0^-n T^n
    shifted = chemkin_export.written_vs_stored_fit(written, SimpleNamespace(a=1459.78, n=3.46761, ea_kj_mol=8.50629, t0_k=300.0))
    assert shifted["a"]["over_half_unit"] > 1
    assert chemkin_export.written_vs_stored_fit(None, kinetics) is None


def test_every_number_in_a_nasa_card_is_compared_with_the_stored_record():
    record = chemkin_export.parse_therm_dat(THERM_DAT)["C1H3"]
    nasa = SimpleNamespace(
        t_low=10.0, t_mid=646.79, t_high=3000.0,
        **{f"a{i}": v for i, v in enumerate(CH3_LOW, 1)}, **{f"b{i}": v for i, v in enumerate(CH3_HIGH, 1)},
    )
    out = chemkin_export.nasa_vs_record(record, nasa)
    assert out["numbers_compared"] == 17 and out["max_difference_over_half_unit"] <= 1
    assert out["min_coefficient_significant_digits"] == 9
    assert (out["t_low_decimals"], out["t_mid_decimals"], out["t_high_decimals"]) == (3, 2, 3)
    nasa.a3 = nasa.a3 + 1e-6  # a stored coefficient the card does not hold
    assert chemkin_export.nasa_vs_record(record, nasa)["max_difference_over_half_unit"] > 1
    assert chemkin_export.nasa_vs_record(record, SimpleNamespace(**{**vars(nasa), "a1": None})) is None
    assert chemkin_export.nasa_vs_record(record, None) is None
    # a card missing a coefficient is not compared on the six it has
    short = {**record, "high_text": record["high_text"][:6]}
    assert chemkin_export.nasa_vs_record(short, nasa) is None


# ---------------------------------------------------------------------------
# the real Cantera load
# ---------------------------------------------------------------------------


def _card(name: str, elements: str, mid: float, low: list[float], high: list[float]) -> str:
    def fmt(values):
        return "".join(f"{v:>15.8E}" for v in values)

    head = f"{name:<18}{'':6}{elements:<20}G{10.0:>10.3f}{3000.0:>10.3f}{mid:>8.2f}{'':6}1"
    coefficients = high + low
    return "\n".join(
        [head, f"{fmt(coefficients[0:5]):<75}    2", f"{fmt(coefficients[5:10]):<75}    3", f"{fmt(coefficients[10:14]):<75}    4"]
    )


CH3_HIGH = [3.24887, 5.01881e-3, -1.26761e-6, 3.21894e-11, 2.01583e-14, 1.6241e4, 3.46319]
CH3_LOW = [3.99122, 4.27522e-4, 9.38093e-6, -1.09443e-8, 4.2631e-12, 1.6145e4, 0.205281]
PLAIN = [3.5, 0.0, 0.0, 0.0, 0.0, -1000.0, 4.0]


def _four_species_therm() -> str:
    cards = [
        _card("H1O1", "O   1H   1", 985.66, PLAIN, PLAIN),
        _card("H2O1", "O   1H   2", 775.53, PLAIN, PLAIN),
        _card("C1H4", "C   1H   4", 616.76, PLAIN, PLAIN),
        _card("C1H3", "C   1H   3", 646.79, CH3_LOW, CH3_HIGH),
    ]
    return "THERMO ALL\n   300.000  1000.000  5000.000\n" + "\n".join(cards) + "\nEND\n"


def test_cantera_loads_the_files_strictly_and_agrees_with_the_hand_calculation(tmp_path):
    (tmp_path / "chem.inp").write_text(CHEM_INP)
    (tmp_path / "therm.dat").write_text(_four_species_therm())

    out = chemkin_export.cantera_load(tmp_path / "chem.inp", tmp_path / "therm.dat", None, 300.0)

    assert out["status"] == "loaded"
    assert out["version"] == ct.__version__ == "3.2.0"
    assert out["species"] == ["C1H3", "C1H4", "H1O1", "H2O1"]
    (reaction,) = out["reactions"]
    assert reaction["equation"] == "C1H4 + H1O1 <=> C1H3 + H2O1" and reaction["order"] == 2
    a, n, ea_cal_mol, r_cal = 1459.8, 3.468, 2033.0521, 1.987204258
    by_hand = a * 300.0**n * math.exp(-ea_cal_mol / (r_cal * 300.0))
    assert reaction["k_forward_cm3_mol_s"] == pytest.approx(by_hand, rel=1e-6)
    record = chemkin_export.parse_therm_dat(_four_species_therm())["C1H3"]
    assert out["h298_kj_mol"]["C1H3"] == pytest.approx(chemkin_export.nasa7_h_kj_mol(record, 298.15), rel=1e-6)
    assert out["h298_kj_mol"]["C1H3"] == pytest.approx(CH3_H298_KJ_MOL, abs=1e-3)


def test_cantera_refuses_a_species_without_thermo_and_the_refusal_is_the_result(tmp_path):
    (tmp_path / "chem.inp").write_text(CHEM_INP)
    (tmp_path / "therm.dat").write_text(THERM_DAT)  # only C1H3

    out = chemkin_export.cantera_load(tmp_path / "chem.inp", tmp_path / "therm.dat", None, 300.0)

    assert out["status"] == "refused"
    assert str(tmp_path) not in out["error"]


def test_the_full_analysis_of_the_exporters_own_files_with_real_cantera_passes_the_builds_checks(db_session, step):
    entry, entries, kinetics, thermo = _seed(db_session)
    _make_exportable(db_session, thermo)
    _release_with_the_use_case(db_session, entries, kinetics, thermo, [])
    request, _ = chemkin_export.resolve_request(db_session)
    files = chemkin_export.export_in_process(db_session, request)

    out = chemkin_export.analyse(db_session, request, files)

    assert out["cantera"]["status"] == "loaded"
    assert step.verify_output(out) == [], str(out["forward_rate"])
    rate = out["forward_rate"]
    assert rate["stored_fit_units"] == "cm3_mol_s" and rate["units"] == "cm3_mol_s"
    assert abs(rate["cantera_over_hand_evaluation_of_written_digits"] - 1.0) < 1e-6
    assert all(row["nasa_vs_record"]["max_difference_over_half_unit"] <= 1 for row in out["species"])
    # a card whose number is not the stored one is refused
    altered = dict(files)
    altered["therm.dat"] = files["therm.dat"].replace(b"3.50000000E+00", b"3.60000000E+00", 1)
    assert altered["therm.dat"] != files["therm.dat"]
    changed = chemkin_export.analyse(db_session, request, altered)
    assert any("NASA number differs" in r for r in step.verify_output(changed))


# ---------------------------------------------------------------------------
# the saved files against the in-process export
# ---------------------------------------------------------------------------


def _saved(db_session, tmp_path, *, with_release=True):
    entry, entries, kinetics, thermo = _seed(db_session)
    _make_exportable(db_session, thermo)
    _release_with_the_use_case(db_session, entries, kinetics, thermo, [])
    request, _ = chemkin_export.resolve_request(db_session)
    directory = tmp_path / "saved"
    directory.mkdir()
    for name, data in chemkin_export.export_in_process(db_session, request).items():
        (directory / name).write_bytes(data)
    (directory / chemkin_export.REQUEST_FILE).write_text(json.dumps({**request, "thermo_refs": list(reversed(request["thermo_refs"]))}))
    return directory, request


def test_saved_files_that_equal_the_in_process_export_match(db_session, tmp_path):
    directory, _ = _saved(db_session, tmp_path)
    result = chemkin_export.check_saved(db_session, directory)
    assert result["match"] is True and result["request_same"] is True
    assert set(result["files"].values()) == {"same"}


def test_one_flipped_byte_in_chem_inp_is_a_mismatch(db_session, tmp_path):
    directory, _ = _saved(db_session, tmp_path)
    data = bytearray((directory / "chem.inp").read_bytes())
    data[-5] ^= 0x01
    (directory / "chem.inp").write_bytes(bytes(data))
    result = chemkin_export.check_saved(db_session, directory)
    assert result["match"] is False and result["files"]["chem.inp"] == "DIFFERS"


def test_a_deleted_therm_dat_is_a_mismatch(db_session, tmp_path):
    directory, _ = _saved(db_session, tmp_path)
    (directory / "therm.dat").unlink()
    result = chemkin_export.check_saved(db_session, directory)
    assert result["match"] is False and result["files"]["therm.dat"] == "present in only one"


def test_a_stray_tran_dat_is_a_mismatch(db_session, tmp_path):
    directory, _ = _saved(db_session, tmp_path)
    (directory / "tran.dat").write_text("TRANSPORT\nEND\n")
    result = chemkin_export.check_saved(db_session, directory)
    assert result["match"] is False and result["files"]["tran.dat"] == "present in only one"


def test_a_changed_thermo_ref_in_the_saved_request_is_a_mismatch(db_session, tmp_path):
    directory, request = _saved(db_session, tmp_path)
    saved = json.loads((directory / chemkin_export.REQUEST_FILE).read_text())
    saved["thermo_refs"][0] = _ref("thm", "z")
    (directory / chemkin_export.REQUEST_FILE).write_text(json.dumps(saved))
    result = chemkin_export.check_saved(db_session, directory)
    assert result["match"] is False and result["request_same"] is False
    assert set(result["files"].values()) == {"same"}


def test_any_other_difference_in_the_saved_request_is_a_mismatch(db_session, tmp_path):
    directory, _ = _saved(db_session, tmp_path)
    saved = json.loads((directory / chemkin_export.REQUEST_FILE).read_text())
    saved["naming_policy"] = "public_ref"
    (directory / chemkin_export.REQUEST_FILE).write_text(json.dumps(saved))
    assert chemkin_export.check_saved(db_session, directory)["match"] is False


# ---------------------------------------------------------------------------
# finding the request
# ---------------------------------------------------------------------------


def test_a_draft_release_is_not_read(db_session):
    entry, entries, kinetics, thermo = _seed(db_session)
    _release_with_the_use_case(db_session, entries, kinetics, thermo, [], publish=False)
    request, reason = chemkin_export.resolve_request(db_session)
    assert request is None and "not published" in reason


def test_a_ch3_record_must_be_both_selected_and_deposited_by_the_rate_records_submission(db_session):
    entry, entries, kinetics, thermo = _seed(db_session)
    # the release selects a [CH3] record that the rate record's submission did not deposit
    elsewhere = make_thermo_scalar(db_session, species_entry=entries["[CH3]"], h298_kj_mol=1.0)
    _release_with_the_use_case(db_session, entries, kinetics, thermo, [elsewhere], selected_ch3=elsewhere)
    request, reason = chemkin_export.resolve_request(db_session)
    assert request is None and "thermo records for [CH3]" in reason
