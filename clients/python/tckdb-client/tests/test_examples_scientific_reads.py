"""Smoke tests for the runnable ``examples/scientific_reads.py`` script.

These don't talk to a backend; they verify that the script imports
cleanly, accepts ``--help``, and that the human-readable pretty
printers lead with public ``*_ref`` handles when refs are present in
the response payload. The full end-to-end behavior is covered by the
backend API tests and the client unit tests.
"""

from __future__ import annotations

import io
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest


EXAMPLE_PATH = (
    Path(__file__).resolve().parents[1] / "examples" / "scientific_reads.py"
)


def _load_module():
    """Import ``scientific_reads`` from the examples folder."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "tckdb_examples_scientific_reads", EXAMPLE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Import / CLI smoke
# ---------------------------------------------------------------------------


def test_example_script_imports():
    module = _load_module()
    # Sanity-check a couple of the renamed/extended public entry points.
    assert hasattr(module, "main")
    assert hasattr(module, "_ref_id")
    assert hasattr(module, "run_thermo_detail_followup")
    assert hasattr(module, "run_lot_ref_followup")
    assert hasattr(module, "run_full_provenance_followup")
    assert hasattr(module, "run_geometry_followup")


def test_geometry_followup_skips_quietly_when_no_geometry_ref_present():
    """When the prior search response has no geometry handle, the
    follow-up returns without raising and without making a request.
    """
    module = _load_module()
    import argparse as _argparse

    args = _argparse.Namespace(include_internal_ids=False, json=False)
    calls = []

    class _FakeClient:
        def get_geometry(self, *a, **kw):  # pragma: no cover — must not run
            calls.append((a, kw))

    # No records → silent skip.
    module.run_geometry_followup(_FakeClient(), args, {"records": []})
    # Records but no geometry block → silent skip.
    module.run_geometry_followup(
        _FakeClient(), args, {"records": [{"geometry": {}}]}
    )
    assert calls == []


def test_geometry_followup_prefers_primary_output_then_input_ref():
    """The follow-up prefers ``primary_output_geometry_ref`` (set for
    opt) and falls back to the first input geometry ref (the SP case).
    """
    module = _load_module()
    import argparse as _argparse

    args = _argparse.Namespace(include_internal_ids=False, json=True)
    seen_handles = []

    class _FakeClient:
        def get_geometry(self, handle, *, include=None):
            seen_handles.append(handle)
            return {
                "request": {
                    "filter": {},
                    "sort": "",
                    "collapse": "all",
                    "include": include or [],
                },
                "geometry_ref": handle,
                "natoms": 0,
                "geom_hash": "0" * 64,
                "format": "cartesian",
                "coordinate_units": "angstrom",
                "symbols": [],
                "coords": [],
                "atoms": [],
                "xyz_text": None,
                "created_at": "2026-05-11T00:00:00Z",
                "provenance": {"produced_by": [], "used_as_input_by": []},
            }

    # Case 1: primary_output_geometry_ref wins.
    module.run_geometry_followup(
        _FakeClient(),
        args,
        {
            "records": [
                {
                    "geometry": {
                        "primary_output_geometry_ref": "geom_out",
                        "input_geometries": [
                            {"geometry_ref": "geom_in", "role": None}
                        ],
                    }
                }
            ]
        },
    )
    # Case 2: no primary output → falls back to first input.
    module.run_geometry_followup(
        _FakeClient(),
        args,
        {
            "records": [
                {
                    "geometry": {
                        "primary_output_geometry_ref": None,
                        "input_geometries": [
                            {"geometry_ref": "geom_in_fb", "role": None}
                        ],
                    }
                }
            ]
        },
    )
    assert seen_handles == ["geom_out", "geom_in_fb"]


def test_example_script_help_runs():
    """``python scientific_reads.py --help`` exits 0 and prints usage."""
    result = subprocess.run(
        [sys.executable, str(EXAMPLE_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "--level-of-theory-ref" in out
    assert "--smiles" in out
    assert "--json" in out
    # Phase D: the opt-in flag for restoring integer IDs is documented.
    assert "--include-internal-ids" in out


def test_includes_helper_appends_internal_ids_only_when_flag_set():
    """``_includes(args, ...)`` appends ``internal_ids`` only on opt-in.

    Phase D: by default scientific reads no longer carry integer ids;
    the example helper threads the opt-in token into every request
    when ``--include-internal-ids`` is supplied.
    """
    module = _load_module()
    import argparse as _argparse

    off = _argparse.Namespace(include_internal_ids=False)
    on = _argparse.Namespace(include_internal_ids=True)

    assert module._includes(off, "review") == ["review"]
    assert module._includes(on, "review") == ["review", "internal_ids"]
    # Idempotent: already-present token is not duplicated.
    assert module._includes(on, "review", "internal_ids") == [
        "review",
        "internal_ids",
    ]


# ---------------------------------------------------------------------------
# Pretty printers — refs lead, ids follow
# ---------------------------------------------------------------------------


def _capture(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


def test_ref_id_helper_leads_with_ref_when_present():
    module = _load_module()
    formatted = module._ref_id(
        {"species_entry_ref": "spe_abc", "species_entry_id": 42},
        "species_entry_ref",
        "species_entry_id",
    )
    assert formatted.startswith("species_entry_ref=spe_abc")
    assert "species_entry_id=42" in formatted
    # Confirm ref appears before id.
    assert formatted.index("species_entry_ref=") < formatted.index(
        "species_entry_id="
    )


def test_ref_id_helper_falls_back_to_id_when_no_ref():
    module = _load_module()
    assert (
        module._ref_id(
            {"species_entry_id": 7},
            "species_entry_ref",
            "species_entry_id",
        )
        == "species_entry_id=7"
    )


def test_print_species_record_leads_with_refs():
    module = _load_module()
    record = {
        "species_id": 12,
        "species_ref": "spc_abc123",
        "canonical_smiles": "CC",
        "inchi_key": "ABCDEFGHIJKLMNOPQRSTUVWXYZ7",
        "charge": 0,
        "multiplicity": 1,
        "entries": [
            {
                "species_entry_id": 31,
                "species_entry_ref": "spe_def456",
                "species_entry_kind": "minimum",
                "electronic_state_kind": "ground",
                "review": {"status": "not_reviewed"},
                "availability": {
                    "has_thermo": True,
                    "has_statmech": False,
                    "has_transport": False,
                    "has_conformers": False,
                    "calculation_count": 1,
                },
            }
        ],
    }
    out = _capture(module._print_species_record, record)
    # species_ref appears before species_id, on the same line.
    species_line = next(
        line for line in out.splitlines() if "species_ref=" in line
    )
    assert species_line.index("species_ref=spc_abc123") < species_line.index(
        "species_id=12"
    )
    # species_entry_ref appears before species_entry_id.
    entry_line = next(
        line for line in out.splitlines() if "species_entry_ref=" in line
    )
    assert entry_line.index("species_entry_ref=spe_def456") < entry_line.index(
        "species_entry_id=31"
    )


def test_print_thermo_record_leads_with_refs():
    module = _load_module()
    record = {
        "species": {"species_entry_id": 31, "species_entry_ref": "spe_def"},
        "thermo": {
            "thermo_id": 7,
            "thermo_ref": "thm_xyz",
            "model_kind": "scalar",
            "review": {"status": "approved"},
            "h298_kj_mol": -10.0,
            "s298_j_mol_k": 200.0,
            "temperature_coverage": {
                "covers_requested_range": True,
                "extrapolation_distance_k": 0.0,
            },
            "evidence_completeness": {"score": 3, "max": 8},
        },
    }
    out = _capture(module._print_thermo_record, record)
    header = out.splitlines()[0]
    assert header.index("species_entry_ref=spe_def") < header.index(
        "species_entry_id=31"
    )
    assert header.index("thermo_ref=thm_xyz") < header.index("thermo_id=7")


def test_print_kinetics_record_leads_with_refs():
    module = _load_module()
    record = {
        "reaction": {
            "reaction_entry_id": 51,
            "reaction_entry_ref": "rxe_abc",
            "matched_direction": "forward",
        },
        "kinetics": {
            "kinetics_id": 14,
            "kinetics_ref": "kin_def",
            "scientific_origin": "computed_chemistry",
            "model_kind": "arrhenius",
            "parameters": {
                "A": 1e10,
                "A_units": "cm3/mol/s",
                "n": 0.0,
                "Ea_kj_mol": 50.0,
            },
            "review": {"status": "approved"},
            "temperature_coverage": {
                "covers_requested_range": True,
                "extrapolation_distance_k": 0.0,
            },
            "provenance": {
                "transition_state_entry_id": 5,
                "transition_state_entry_ref": "tse_aaa",
                "ts_opt_calculation_id": 22,
                "ts_opt_calculation_ref": "calc_opt",
                "ts_freq_calculation_id": 23,
                "ts_freq_calculation_ref": "calc_freq",
                "ts_sp_calculation_id": 24,
                "ts_sp_calculation_ref": "calc_sp",
            },
        },
    }
    out = _capture(module._print_kinetics_record, record)
    header = out.splitlines()[0]
    assert header.index("reaction_entry_ref=rxe_abc") < header.index(
        "reaction_entry_id=51"
    )
    assert header.index("kinetics_ref=kin_def") < header.index(
        "kinetics_id=14"
    )
    # Provenance line names every ref alongside its id.
    assert "transition_state_entry_ref=tse_aaa" in out
    assert "ts_opt_calculation_ref=calc_opt" in out
    assert "ts_freq_calculation_ref=calc_freq" in out
    assert "ts_sp_calculation_ref=calc_sp" in out


# ---------------------------------------------------------------------------
# Phase D: printers tolerate missing *_id fields (refs-only responses)
# ---------------------------------------------------------------------------


def test_print_species_record_works_without_ids():
    """Phase D default responses omit ``*_id`` keys entirely."""
    module = _load_module()
    record = {
        # No species_id; only the ref.
        "species_ref": "spc_abc123",
        "canonical_smiles": "CC",
        "inchi_key": "ABCDEFGHIJKLMNOPQRSTUVWXYZ7",
        "charge": 0,
        "multiplicity": 1,
        "entries": [
            {
                "species_entry_ref": "spe_def456",
                "species_entry_kind": "minimum",
                "electronic_state_kind": "ground",
                "review": {"status": "not_reviewed"},
                "availability": {
                    "has_thermo": True,
                    "has_statmech": False,
                    "has_transport": False,
                    "has_conformers": False,
                    "calculation_count": 0,
                },
            }
        ],
    }
    # Should not raise — printers must tolerate missing ids.
    out = _capture(module._print_species_record, record)
    assert "species_ref=spc_abc123" in out
    assert "species_entry_ref=spe_def456" in out
    # ID strings do not appear.
    assert "species_id=" not in out
    assert "species_entry_id=" not in out


def test_print_kinetics_record_works_without_ts_chain_ids():
    """Phase D: non-TS provenance has refs=null and no integer ids."""
    module = _load_module()
    record = {
        "reaction": {
            "reaction_entry_ref": "rxe_abc",
            "matched_direction": "forward",
        },
        "kinetics": {
            "kinetics_ref": "kin_def",
            "scientific_origin": "experimental",
            "model_kind": "arrhenius",
            "parameters": {
                "A": 1e10,
                "A_units": "cm3/mol/s",
                "n": 0.0,
                "Ea_kj_mol": 50.0,
            },
            "review": {"status": "approved"},
            "temperature_coverage": {
                "covers_requested_range": True,
                "extrapolation_distance_k": 0.0,
            },
            "provenance": {
                # No ids; non-TS-backed kinetics ref siblings are null.
                "transition_state_entry_ref": None,
                "ts_opt_calculation_ref": None,
                "ts_freq_calculation_ref": None,
                "ts_sp_calculation_ref": None,
            },
        },
    }
    out = _capture(module._print_kinetics_record, record)
    # The script falls back to the non-TS message because every TS-chain
    # ref is None and no id is present.
    assert "non-TS-backed" in out


# ---------------------------------------------------------------------------
# Section selection CLI: --only / --skip / --no-followups
# ---------------------------------------------------------------------------


def test_help_documents_section_flags():
    """``--only``, ``--skip``, ``--no-followups``, ``--calculation-type``,
    and ``--ranking`` are all surfaced in CLI help text.
    """
    result = subprocess.run(
        [sys.executable, str(EXAMPLE_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for flag in (
        "--only",
        "--skip",
        "--no-followups",
        "--calculation-type",
        "--ranking",
    ):
        assert flag in out, f"missing CLI flag {flag!r} in --help output"


def test_parse_section_set_handles_comma_separated_input():
    module = _load_module()
    parsed = module.parse_section_set("species, thermo,calculations")
    assert parsed == {"species", "thermo", "calculations"}


def test_parse_section_set_expands_all():
    module = _load_module()
    parsed = module.parse_section_set("all")
    assert parsed == set(module.ALL_SECTIONS)


def test_parse_section_set_combines_all_with_specifics():
    """``all,foo`` keeps any extra (legal) tokens that are listed
    alongside ``all`` — useful for combined include lists, mirrors
    backend ``include=all,internal_ids`` semantics.
    """
    module = _load_module()
    # All sections legal; the combination is idempotent.
    parsed = module.parse_section_set("all,geometry")
    assert parsed == set(module.ALL_SECTIONS)


def test_parse_section_set_rejects_unknown_token():
    import argparse

    module = _load_module()
    with pytest.raises(argparse.ArgumentTypeError):
        module.parse_section_set("species,nonsense")


def test_parse_section_set_empty_returns_empty_set():
    module = _load_module()
    assert module.parse_section_set(None) == set()
    assert module.parse_section_set("") == set()
    assert module.parse_section_set("  ") == set()


def _ns(**overrides):
    """Build an argparse.Namespace with default flag values for tests.

    Overrides win — callers supplying ``only=…`` / ``skip=…`` /
    ``no_followups=…`` replace the defaults rather than collide.
    """
    import argparse

    defaults = {"only": None, "skip": None, "no_followups": False}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_resolve_sections_default_is_all():
    module = _load_module()
    assert module.resolve_sections(_ns()) == set(module.ALL_SECTIONS)


def test_resolve_sections_with_no_followups_strips_followup_subset():
    module = _load_module()
    selected = module.resolve_sections(_ns(no_followups=True))
    assert selected == set(module.PRIMARY_SECTIONS)
    # Make sure no follow-ups leaked through.
    assert selected.isdisjoint(module.FOLLOWUP_SECTIONS)


def test_resolve_sections_only_species_returns_species_only():
    module = _load_module()
    selected = module.resolve_sections(_ns(only="species"))
    assert selected == {"species"}


def test_resolve_sections_only_calculations_geometry():
    module = _load_module()
    selected = module.resolve_sections(_ns(only="calculations,geometry"))
    assert selected == {"calculations", "geometry"}


def test_resolve_sections_skip_thermo_removes_thermo_only():
    module = _load_module()
    selected = module.resolve_sections(_ns(skip="thermo"))
    assert "thermo" not in selected
    assert "thermo-detail" in selected  # not asked to be removed
    assert selected == set(module.ALL_SECTIONS) - {"thermo"}


def test_resolve_sections_only_plus_no_followups_then_skip():
    """``--only`` then ``--no-followups`` then ``--skip`` apply in order."""
    module = _load_module()
    selected = module.resolve_sections(
        _ns(only="calculations,geometry,lot-followup", no_followups=True, skip="calculations")
    )
    # only → {calculations, geometry, lot-followup}
    # no_followups → {calculations}
    # skip=calculations → {}
    assert selected == set()


def test_should_run_predicate_matches_selected_set():
    module = _load_module()
    selected = {"species", "calculations"}
    assert module.should_run("species", selected) is True
    assert module.should_run("calculations", selected) is True
    assert module.should_run("geometry", selected) is False


# ---------------------------------------------------------------------------
# main() integration: dependency-skip cases are quiet and crash-free.
# ---------------------------------------------------------------------------


def test_main_with_only_species_makes_one_call(monkeypatch):
    """``--only species`` must invoke only ``run_species_search``."""
    module = _load_module()
    calls: list[str] = []

    def _record(name: str):
        def fn(*_a, **_kw):
            calls.append(name)
            return None

        return fn

    # Replace every run_* with a tracking stub so we don't touch HTTP.
    for name in (
        "run_species_search",
        "run_thermo_search",
        "run_thermo_detail_followup",
        "run_species_calculations_search",
        "run_lot_ref_followup",
        "run_geometry_followup",
        "run_reaction_search",
        "run_kinetics_search",
        "run_full_provenance_followup",
    ):
        monkeypatch.setattr(module, name, _record(name))
    # Don't open a real HTTP client.
    monkeypatch.setattr(
        module, "TCKDBClient", lambda *a, **kw: type("C", (), {"close": lambda self: None})()
    )

    rc = module.main(
        [
            "--base-url",
            "http://example.test/api/v1",
            "--smiles",
            "O",
            "--only",
            "species",
        ]
    )
    assert rc == 0
    assert calls == ["run_species_search"]


def test_main_no_followups_runs_primaries_only(monkeypatch):
    module = _load_module()
    calls: list[str] = []

    def _record(name: str):
        def fn(*_a, **_kw):
            calls.append(name)
            return None

        return fn

    for name in (
        "run_species_search",
        "run_thermo_search",
        "run_thermo_detail_followup",
        "run_species_calculations_search",
        "run_lot_ref_followup",
        "run_geometry_followup",
        "run_reaction_search",
        "run_kinetics_search",
        "run_full_provenance_followup",
    ):
        monkeypatch.setattr(module, name, _record(name))
    monkeypatch.setattr(
        module, "TCKDBClient", lambda *a, **kw: type("C", (), {"close": lambda self: None})()
    )

    # No reactant/product → reaction-side primaries silently skip.
    rc = module.main(
        [
            "--base-url",
            "http://example.test/api/v1",
            "--smiles",
            "O",
            "--no-followups",
        ]
    )
    assert rc == 0
    # Only species-side primaries; no follow-ups ran.
    assert "run_thermo_detail_followup" not in calls
    assert "run_lot_ref_followup" not in calls
    assert "run_geometry_followup" not in calls
    assert "run_full_provenance_followup" not in calls
    # Reaction-side primaries skip because reactant/product is empty.
    assert "run_reaction_search" not in calls
    assert "run_kinetics_search" not in calls
    # Species-side primaries did run.
    assert "run_species_search" in calls
    assert "run_thermo_search" in calls
    assert "run_species_calculations_search" in calls


def test_main_geometry_followup_skips_when_calculations_disabled(monkeypatch):
    """Selecting only ``geometry`` (without ``calculations``) must skip
    cleanly — there's no calcs response to extract a geometry handle from.
    """
    module = _load_module()
    calls: list[str] = []

    def _record(name: str):
        def fn(*_a, **_kw):
            calls.append(name)
            return None

        return fn

    for name in (
        "run_species_search",
        "run_thermo_search",
        "run_thermo_detail_followup",
        "run_species_calculations_search",
        "run_lot_ref_followup",
        "run_geometry_followup",
        "run_reaction_search",
        "run_kinetics_search",
        "run_full_provenance_followup",
    ):
        monkeypatch.setattr(module, name, _record(name))
    monkeypatch.setattr(
        module, "TCKDBClient", lambda *a, **kw: type("C", (), {"close": lambda self: None})()
    )

    rc = module.main(
        [
            "--base-url",
            "http://example.test/api/v1",
            "--smiles",
            "O",
            "--only",
            "geometry",
        ]
    )
    assert rc == 0
    assert calls == []  # nothing actually ran


def test_main_reactions_skip_when_no_reactants_supplied(monkeypatch):
    """``--only reactions`` with no reactant/product silently skips."""
    module = _load_module()
    calls: list[str] = []

    def _record(name: str):
        def fn(*_a, **_kw):
            calls.append(name)
            return None

        return fn

    for name in (
        "run_species_search",
        "run_thermo_search",
        "run_thermo_detail_followup",
        "run_species_calculations_search",
        "run_lot_ref_followup",
        "run_geometry_followup",
        "run_reaction_search",
        "run_kinetics_search",
        "run_full_provenance_followup",
    ):
        monkeypatch.setattr(module, name, _record(name))
    monkeypatch.setattr(
        module, "TCKDBClient", lambda *a, **kw: type("C", (), {"close": lambda self: None})()
    )

    rc = module.main(
        [
            "--base-url",
            "http://example.test/api/v1",
            "--smiles",
            "O",
            "--only",
            "reactions,kinetics,full",
        ]
    )
    assert rc == 0
    assert calls == []


def test_calculation_type_and_ranking_flags_thread_through(monkeypatch):
    """The new --calculation-type and --ranking flags reach the
    underlying client call instead of the previous hardcoded constants.
    """
    module = _load_module()
    seen: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def close(self):
            pass

        def search_species_calculations(self, **kwargs):
            seen.update(kwargs)
            return {
                "records": [],
                "pagination": {"returned": 0, "total": 0},
                "request": {
                    "filter": {},
                    "sort": "",
                    "collapse": "first",
                    "include": [],
                },
                "review_summary": {},
            }

    monkeypatch.setattr(module, "TCKDBClient", _FakeClient)
    rc = module.main(
        [
            "--base-url",
            "http://example.test/api/v1",
            "--smiles",
            "O",
            "--only",
            "calculations",
            "--calculation-type",
            "opt",
            "--ranking",
            "latest",
            "--json",
        ]
    )
    assert rc == 0
    assert seen.get("calculation_type") == "opt"
    assert seen.get("ranking") == "latest"
