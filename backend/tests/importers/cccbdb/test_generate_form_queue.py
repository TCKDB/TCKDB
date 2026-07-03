"""Tests for the CCCBDB form-queue generator CLI.

All tests are offline. The generator never fetches CCCBDB and the
fixtures live under ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.importers.cccbdb.form_resolver import FormQueueRecord
from scripts.cccbdb_generate_form_queue import (
    CatalogEntry,
    QueueGenFilters,
    generate_queue,
    generate_species_key,
    load_catalog_entries,
    main,
    write_queue_file,
)

# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------


def _write_catalog(
    tmp_path: Path,
    rows: list[dict],
    *,
    key: str = "entries",
    name: str = "catalog.json",
) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({key: rows}))
    return path


class TestCatalogLoading:
    def test_loads_entries_layout(self, tmp_path):
        path = _write_catalog(
            tmp_path,
            [
                {
                    "formula": "H2O",
                    "name": "Water",
                    "inchikey": "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
                    "smiles": "O",
                }
            ],
        )
        entries = load_catalog_entries(path)
        assert len(entries) == 1
        assert entries[0].formula == "H2O"
        assert entries[0].name == "Water"

    def test_loads_records_layout(self, tmp_path):
        """Hand-authored ``{"records": [...]}`` queue inputs are also
        valid catalog inputs."""

        path = _write_catalog(
            tmp_path,
            [{"formula": "CH4", "name": "Methane"}],
            key="records",
        )
        entries = load_catalog_entries(path)
        assert entries[0].formula == "CH4"

    def test_rejects_wrong_top_level(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"foo": []}))
        with pytest.raises(ValueError, match="entries.*records"):
            load_catalog_entries(path)

    def test_rejects_non_list_entries(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"entries": "not-a-list"}))
        with pytest.raises(ValueError, match="list of objects"):
            load_catalog_entries(path)

    def test_accepts_casno_alias(self, tmp_path):
        """CCCBDB sometimes labels CAS as ``casno`` (e.g.
        ``choosex.asp``). The catalog loader honors the alias."""

        path = _write_catalog(
            tmp_path,
            [{"formula": "H2O", "name": "Water", "casno": "7732-18-5"}],
        )
        entries = load_catalog_entries(path)
        assert entries[0].cas_number == "7732-18-5"


# ---------------------------------------------------------------------------
# species_key generation
# ---------------------------------------------------------------------------


class TestSpeciesKey:
    def test_uses_name_when_present(self):
        used: set[str] = set()
        key = generate_species_key(
            CatalogEntry(formula="H2O", name="Water"), used=used
        )
        assert key == "water"
        assert "water" in used

    def test_falls_back_to_formula(self):
        used: set[str] = set()
        key = generate_species_key(
            CatalogEntry(formula="H2O", name=None), used=used
        )
        assert key == "h2o"

    def test_inchikey_breaks_collision(self):
        used = {"ethanol"}
        key = generate_species_key(
            CatalogEntry(
                formula="C2H6O", name="Ethanol",
                inchikey="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            ),
            used=used,
        )
        # The connectivity-hash prefix is lowercased and 14 chars.
        assert key == "ethanol_lfqscwfljhtthz"
        assert key in used

    def test_numeric_suffix_breaks_collision_without_inchikey(self):
        used = {"x"}
        key = generate_species_key(
            CatalogEntry(formula="X", name=None), used=used
        )
        assert key == "x_2"

    def test_filesystem_safe(self):
        used: set[str] = set()
        # Spaces, slashes, and unicode collapse to underscores.
        key = generate_species_key(
            CatalogEntry(
                formula="X", name="Strange name / with weird chars",
            ),
            used=used,
        )
        # Allowed chars: a-z, 0-9, . _ -
        assert all(c.isalnum() or c in "._-" for c in key)
        assert key  # non-empty


# ---------------------------------------------------------------------------
# Queue generation
# ---------------------------------------------------------------------------


def _entries() -> list[CatalogEntry]:
    return [
        CatalogEntry(
            formula="H2O", name="Water", smiles="O",
            inchi="InChI=1S/H2O/h1H2",
            inchikey="XLYOFNOQVPJJNP-UHFFFAOYSA-N",
        ),
        CatalogEntry(
            formula="CH4", name="Methane", smiles="C",
            inchikey="VNWKTOKETHGBQD-UHFFFAOYSA-N",
        ),
        CatalogEntry(
            formula="C2H6O", name="Ethanol", smiles="CCO",
            inchikey="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        ),
        CatalogEntry(
            formula="C2H6O", name="Dimethyl ether", smiles="COC",
            inchikey="LCGLNKUTAGEVQW-UHFFFAOYSA-N",
        ),
    ]


class TestQueueGeneration:
    def test_sets_target_kind_and_entry_url_on_every_record(self):
        result = generate_queue(
            _entries(),
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(),
        )
        assert all(
            r.target_kind == "atomization_energy"
            and r.entry_url == "https://cccbdb.nist.gov/ea1x.asp"
            for r in result.records
        )

    def test_preserves_identity_fields(self):
        result = generate_queue(
            _entries(),
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(),
        )
        water = next(r for r in result.records if r.name == "Water")
        assert water.formula == "H2O"
        assert water.inchi == "InChI=1S/H2O/h1H2"
        assert water.inchikey == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        assert water.smiles == "O"

    def test_distinguishes_isomers_via_species_key(self):
        result = generate_queue(
            _entries(),
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(),
        )
        c2h6o_records = [r for r in result.records if r.formula == "C2H6O"]
        assert len(c2h6o_records) == 2
        # Distinct species_keys for the two isomers.
        keys = {r.species_key for r in c2h6o_records}
        assert keys == {"ethanol", "dimethyl_ether"}

    def test_deduplicates_repeated_catalog_entries(self):
        rows = [*_entries(), _entries()[0]]  # duplicate water row
        result = generate_queue(
            rows,
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(),
        )
        assert result.written == 4  # not 5
        assert result.skipped_duplicate == 1

    def test_skips_entries_without_formula(self):
        rows = [*_entries(), CatalogEntry(formula=None, name="Mystery")]
        result = generate_queue(
            rows,
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(),
        )
        assert result.skipped_no_formula == 1
        assert all(r.formula for r in result.records)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestFilters:
    def test_limit(self):
        result = generate_queue(
            _entries(),
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(limit=2),
        )
        assert result.written == 2

    def test_offset(self):
        result = generate_queue(
            _entries(),
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(offset=2),
        )
        # Skips Water + Methane; keeps Ethanol + DME.
        names = [r.name for r in result.records]
        assert names == ["Ethanol", "Dimethyl ether"]

    def test_formula_filter(self):
        result = generate_queue(
            _entries(),
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(formulas=("C2H6O",)),
        )
        assert {r.formula for r in result.records} == {"C2H6O"}
        assert result.written == 2

    def test_name_contains_filter(self):
        result = generate_queue(
            _entries(),
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(name_contains="ether"),
        )
        names = {r.name for r in result.records}
        assert names == {"Dimethyl ether"}

    def test_require_inchikey_skips_entries_without_key(self):
        rows = [
            CatalogEntry(formula="X", name="Anonymous", inchikey=None),
            CatalogEntry(
                formula="H2O", name="Water",
                inchikey="XLYOFNOQVPJJNP-UHFFFAOYSA-N",
            ),
        ]
        result = generate_queue(
            rows,
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(require_inchikey=True),
        )
        assert len(result.records) == 1
        assert result.records[0].name == "Water"


# ---------------------------------------------------------------------------
# Output shape + safety
# ---------------------------------------------------------------------------


class TestOutputShape:
    def test_write_queue_file_round_trips_through_form_queue_record(
        self, tmp_path
    ):
        result = generate_queue(
            _entries(),
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(),
        )
        out = tmp_path / "form_queue.json"
        write_queue_file(result, out)

        data = json.loads(out.read_text())
        assert "records" in data
        assert len(data["records"]) == result.written

        # Each record must validate through the form-resolver's
        # FormQueueRecord.from_dict contract.
        for rec in data["records"]:
            queue_record = FormQueueRecord.from_dict(rec)
            assert queue_record.formula
            assert queue_record.target_kind == "atomization_energy"
            assert queue_record.entry_url == "https://cccbdb.nist.gov/ea1x.asp"

    def test_does_not_use_raw_href_as_data_url(self, tmp_path):
        """``inchix.asp`` rows carry ``raw_href`` for audit, NOT as a
        trusted data-page URL. The generator must NEVER copy
        ``raw_href`` into ``entry_url`` or any other URL field on the
        queue record."""

        rows = [
            CatalogEntry(
                formula="H2O", name="Water",
                inchikey="XLYOFNOQVPJJNP-UHFFFAOYSA-N",
                raw_href="getformx.asp?inchi=foo",
            )
        ]
        result = generate_queue(
            rows,
            target_kind="atomization_energy",
            entry_url="https://cccbdb.nist.gov/ea1x.asp",
            filters=QueueGenFilters(),
        )
        out = tmp_path / "form_queue.json"
        write_queue_file(result, out)
        data = json.loads(out.read_text())
        for rec in data["records"]:
            # entry_url must be the maintainer-supplied URL, NOT raw_href.
            assert rec["entry_url"] == "https://cccbdb.nist.gov/ea1x.asp"
            assert "raw_href" not in rec
            assert "trusted_property_url" not in rec


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestCli:
    def _write_catalog(self, tmp_path: Path) -> Path:
        return _write_catalog(
            tmp_path,
            [
                {
                    "formula": "H2O", "name": "Water",
                    "inchikey": "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
                    "smiles": "O",
                },
                {
                    "formula": "CH4", "name": "Methane",
                    "inchikey": "VNWKTOKETHGBQD-UHFFFAOYSA-N",
                    "smiles": "C",
                },
            ],
        )

    def test_cli_writes_form_queue(self, tmp_path):
        catalog = self._write_catalog(tmp_path)
        out = tmp_path / "form_queue.json"
        rc = main(
            [
                "--catalog-json", str(catalog),
                "--output", str(out),
                "--target-kind", "atomization_energy",
                "--entry-url", "https://cccbdb.nist.gov/ea1x.asp",
            ]
        )
        assert rc == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert len(data["records"]) == 2

    def test_cli_limit(self, tmp_path):
        catalog = self._write_catalog(tmp_path)
        out = tmp_path / "form_queue.json"
        main(
            [
                "--catalog-json", str(catalog),
                "--output", str(out),
                "--target-kind", "atomization_energy",
                "--entry-url", "https://cccbdb.nist.gov/ea1x.asp",
                "--limit", "1",
            ]
        )
        data = json.loads(out.read_text())
        assert len(data["records"]) == 1

    def test_cli_returns_2_on_missing_catalog(self, tmp_path):
        rc = main(
            [
                "--catalog-json", str(tmp_path / "nope.json"),
                "--output", str(tmp_path / "out.json"),
                "--target-kind", "atomization_energy",
                "--entry-url", "https://cccbdb.nist.gov/ea1x.asp",
            ]
        )
        assert rc == 2

    def test_cli_returns_2_on_bad_catalog_layout(self, tmp_path):
        catalog = tmp_path / "bad.json"
        catalog.write_text(json.dumps({"foo": []}))
        rc = main(
            [
                "--catalog-json", str(catalog),
                "--output", str(tmp_path / "out.json"),
                "--target-kind", "atomization_energy",
                "--entry-url", "https://cccbdb.nist.gov/ea1x.asp",
            ]
        )
        assert rc == 2


# ---------------------------------------------------------------------------
# Structural / no-network invariant
# ---------------------------------------------------------------------------


def test_generator_does_not_import_fetchers_or_resolvers():
    """The generator must NOT pull in CCCBDB snapshot/resolver code —
    its only inputs come from disk."""

    from scripts import cccbdb_generate_form_queue as mod

    forbidden = (
        "run_snapshot",
        "HttpFetcher",
        "run_form_resolver_queue",
        "RequestsSession",
    )
    for name in forbidden:
        assert name not in mod.__dict__, (
            f"generator leaked network/resolver symbol {name!r}"
        )
