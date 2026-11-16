"""The CHEMKIN export of the snapshot use case: the generator's own logic and the build step's checks.

None of these tests needs Cantera. The Cantera load itself (strict ``ck2yaml``, k(300 K) and H298 as Cantera
evaluates them) runs in the build, and the build refuses a snapshot whose output does not show it loaded.
"""

from __future__ import annotations

import io
import json
import math
import sys
import zipfile
from pathlib import Path

import pytest

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, ArrheniusAUnits, RecordReviewStatus, SubmissionRecordType
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
    assert parsed["reactions"] == [{"equation": "C1H4 + H1O1 <=> C1H3 + H2O1", "a": 1459.8, "n": 3.468, "ea": 2033.0521}]


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


def _release_with_the_use_case(db_session, entries, kinetics, thermo, decoys):
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
        (SubmissionRecordType.thermo, thermo["[CH3]"].id, SubmissionRecordType.species_entry, entries["[CH3]"].id),
    ):
        add_selection(
            db_session, release=release, record_type=record_type, record_id=record_id,
            subject_type=subject_type, subject_id=subject_id, rationale="r", selected_by=curator.id,
        )
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
    for record in thermo.values():
        attach_thermo_nasa(db_session, thermo=record)
    _release_with_the_use_case(db_session, entries, kinetics, thermo, [])
    request, _ = chemkin_export.resolve_request(db_session)

    files = chemkin_export.export_in_process(db_session, request)

    assert {"chem.inp", "therm.dat", "manifest.json"} <= set(files)
    assert all(ref in files["chem.inp"].decode() for ref in (entries[s].species.public_ref for s in ("C", "[CH3]")))
    parsed = chemkin_export.parse_chem_inp(files["chem.inp"].decode())
    assert sorted(s["smiles"] for s in parsed["species"]) == ["C", "O", "[CH3]", "[OH]"]
    assert len(parsed["reactions"]) == 1
    # The factory thermo rows carry no polynomial the exporter writes; the build checks therm.dat against the real deposit.
    assert files["therm.dat"].decode().startswith("THERMO")


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


def _config(runs=("run/a",), plan=None):
    return {"sources": {"arc_runs": {"runs": list(runs)}}, "chemkin_export": plan}


def test_the_plan_must_name_a_deposited_run_and_distinct_species(step):
    assert step.problems(_config(plan=None)) == []
    assert step.problems(_config(plan={"run": "run/a", "species": ["C", "[OH]"]})) == []
    assert any("not in sources.arc_runs.runs" in p for p in step.problems(_config(plan={"run": "run/z", "species": ["C"]})))
    assert any("species" in p for p in step.problems(_config(plan={"run": "run/a", "species": ["C", "C"]})))
    assert any("species" in p for p in step.problems(_config(plan={"run": "run/a", "species": []})))
    assert any("unknown key" in p for p in step.problems(_config(plan={"run": "run/a", "species": ["C"], "pick": 1})))


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
        "forward_rate": {"cantera_k_forward": 1.0},
        "ch3_h298": {"exported_nasa_h298_kj_mol": 1.0},
        "species": [{"smiles": "C", "thermo_refs_named": ["thm_a"], "has_nasa_in_therm_dat": True}],
        "named_thermo_refs_without_a_species_in_chem_inp": [],
    }


def test_the_build_accepts_only_an_output_that_shows_the_files_loaded_and_checked(step):
    assert step.verify_output(_good_output()) == []
    assert step.verify_output({"export": "not_supplied"})
    absent = _good_output()
    absent["cantera"] = {"status": "cantera_not_installed", "detail": "no module"}
    assert any("Cantera did not load" in r for r in step.verify_output(absent))
    refused = _good_output()
    refused["cantera"] = {"status": "refused", "error": "ck2yaml: bad"}
    assert any("refused" in r for r in step.verify_output(refused))
    no_rate = _good_output()
    no_rate["forward_rate"] = {"cantera_k_forward": None}
    assert step.verify_output(no_rate)
    no_nasa = _good_output()
    no_nasa["species"][0]["has_nasa_in_therm_dat"] = False
    assert step.verify_output(no_nasa)


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
