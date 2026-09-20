"""Opt-in smoke test against the REAL, pinned NIST TRC ThermoML Archive.

Every other test in this package runs against hand-authored fixtures
(``fixtures/README.md``) -- this is the one place that reads real
archive bytes, and it is opt-in for two reasons: the archive is 189 MB
(not something CI should download on every run), and its terms forbid
committing article content (see ``TERMS_TEXT`` in
``app/importers/thermoml/__init__.py``), so it can never be a fixture.

Set ``TCKDB_THERMOML_ARCHIVE`` to a local path to a copy of
``ThermoML.v2020-09-30.tgz`` to run this test. If the variable is
unset, OR the file at that path does not hash to
:data:`app.importers.thermoml.ARCHIVE_SHA256`, every test here SKIPS
with a visible reason -- never a silent pass, and never a fetch: this
test never calls :func:`app.importers.thermoml.archive.fetch_archive`
itself, so it never triggers a 189 MB download as a side effect of
running the suite.

Assertions here are deliberately about COUNTS and STATE FIELDS (payload
counts, state_basis, pressure source/range, uncertainty kind/coverage/
confidence/assessor, origin, method_note, unsupported/rejected
reasons) -- never a specific Cp/temperature/pressure NUMBER, which
would mean a real article's data point living in version control.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.importers.thermoml import ARCHIVE_SHA256
from app.importers.thermoml.archive import select_article
from app.importers.thermoml.mapping import map_document
from app.importers.thermoml.parser import parse_thermoml_document
from app.importers.thermoml.validate import validate_bytes

_ENV_VAR = "TCKDB_THERMOML_ARCHIVE"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_path() -> Path | None:
    raw = os.environ.get(_ENV_VAR)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        return None
    if _sha256_file(path) != ARCHIVE_SHA256:
        return None
    return path


_ARCHIVE = _archive_path()

pytestmark = pytest.mark.skipif(
    _ARCHIVE is None,
    reason=(
        f"real-archive smoke test skipped: set {_ENV_VAR} to a local copy of "
        "ThermoML.v2020-09-30.tgz that hashes to ARCHIVE_SHA256 to run it "
        "(the archive is 189 MB and is never fetched automatically by this "
        "test)"
    ),
)


def _map_real_doi(doi: str):
    assert _ARCHIVE is not None  # narrows for mypy; pytestmark already skipped
    article = select_article(_ARCHIVE, doi)
    schema_report = validate_bytes(article.xml)
    assert schema_report.valid, f"{doi}: real article is not schema-valid: {schema_report.errors}"
    document = parse_thermoml_document(article.xml)
    return map_document(document, doi=doi)


class TestBenzenePilot:
    """DOI 10.1016/j.jct.2013.08.022 -- the pilot article (plan C0/C2):
    "Ideal gas" at a 100 kPa Constraint, sMethodName "statistical
    thermodynamics", combined expanded uncertainty with a level of
    confidence and no coverage factor, 12 values 200-1000 K."""

    DOI = "10.1016/j.jct.2013.08.022"

    def test_payload_count_and_temperature_span(self):
        result = _map_real_doi(self.DOI)
        assert len(result.payloads) == 12
        temps = [p.temperature_k for p in result.payloads]
        assert min(temps) == 200.0
        assert max(temps) == 1000.0

    def test_state_basis_is_ideal_gas(self):
        result = _map_real_doi(self.DOI)
        assert {p.state_basis.value for p in result.payloads} == {"ideal_gas"}

    def test_pressure_is_constraint_sourced_and_constant(self):
        result = _map_real_doi(self.DOI)
        sources = {p.raw_payload_json["thermoml"]["pressure_source"] for p in result.payloads}
        assert sources == {"constraint"}
        pressures = {p.pressure_bar for p in result.payloads}
        assert pressures == {1.0}  # 100 kPa Constraint -> 1.0 bar, every row

    def test_uncertainty_is_combined_expanded_no_coverage_factor(self):
        result = _map_real_doi(self.DOI)
        for payload in result.payloads:
            assert payload.uncertainty_kind is not None
            assert payload.uncertainty_kind.value == "combined_expanded"
            assert payload.uncertainty_coverage_factor is None
            assert payload.uncertainty_level_of_confidence_pct == 95.0
            assert payload.uncertainty_assessor is not None
            assert payload.uncertainty_assessor.value == "source_evaluator"

    def test_origin_is_computed_via_the_smethodname_allowlist(self):
        result = _map_real_doi(self.DOI)
        for payload in result.payloads:
            assert payload.scientific_origin.value == "computed"
            assert payload.method_note == "statistical thermodynamics"
            assert "origin.smethodname_allowlist.v1" in payload.raw_payload_json["mapping"]["rules"]

    def test_no_rejections_three_non_cp_properties_unsupported(self):
        result = _map_real_doi(self.DOI)
        assert result.report.rejected == []
        assert len(result.report.unsupported) == 3
        assert {u["reason"] for u in result.report.unsupported} == {"not_heat_capacity_cp"}


class TestFluoroethaneVariablePressure:
    """DOI 10.1016/j.fluid.2016.07.034: real-gas "Gas" phase, pressure
    carried by a per-row Variable (NOT a fixed 101.325 kPa Constraint),
    38 flow-calorimetry values."""

    DOI = "10.1016/j.fluid.2016.07.034"

    def test_payload_count_and_temperature_span(self):
        result = _map_real_doi(self.DOI)
        assert len(result.payloads) == 38
        temps = [p.temperature_k for p in result.payloads]
        assert min(temps) == pytest.approx(315.33)
        assert max(temps) == pytest.approx(365.75)

    def test_state_basis_is_real_gas(self):
        result = _map_real_doi(self.DOI)
        assert {p.state_basis.value for p in result.payloads} == {"real_gas"}

    def test_pressure_is_variable_sourced_and_ranges_1020_to_3400_kpa(self):
        result = _map_real_doi(self.DOI)
        sources = {p.raw_payload_json["thermoml"]["pressure_source"] for p in result.payloads}
        assert sources == {"variable"}
        pressures = [p.pressure_bar for p in result.payloads]
        assert len(set(pressures)) > 1, "pressure must vary row to row, not be a single constant"
        assert min(pressures) == pytest.approx(1020 / 100.0)
        assert max(pressures) == pytest.approx(3400 / 100.0)

    def test_origin_is_experimental_via_emethodname(self):
        result = _map_real_doi(self.DOI)
        for payload in result.payloads:
            assert payload.scientific_origin.value == "experimental"
            assert payload.method_note == "Flow calorimetry"

    def test_no_rejections_liquid_blocks_unsupported(self):
        result = _map_real_doi(self.DOI)
        assert result.report.rejected == []
        assert {u["reason"] for u in result.report.unsupported} == {"unsupported_phase"}


class TestJctTwoComponentsInOneArticle:
    """DOI 10.1016/j.jct.2011.06.001: TWO distinct single-component Cp
    blocks in one article (ferrocene and nickelocene, both keyed by
    RegNum/nOrgNum), Crystal-phase Cp blocks correctly unsupported."""

    DOI = "10.1016/j.jct.2011.06.001"

    def test_two_distinct_compounds_each_with_61_values(self):
        result = _map_real_doi(self.DOI)
        assert len(result.payloads) == 122
        assert len(result.report.identity) == 2
        keys = {ident["inchikey"] for ident in result.report.identity}
        assert keys == {"DFRHTHSZMBROSH-UHFFFAOYSA-N", "KZPXREABEBSAQM-UHFFFAOYSA-N"}

    def test_state_basis_is_real_gas_pressure_constraint_sourced(self):
        result = _map_real_doi(self.DOI)
        assert {p.state_basis.value for p in result.payloads} == {"real_gas"}
        sources = {p.raw_payload_json["thermoml"]["pressure_source"] for p in result.payloads}
        assert sources == {"constraint"}

    def test_crystal_phase_blocks_are_unsupported_not_rejected(self):
        result = _map_real_doi(self.DOI)
        assert result.report.rejected == []
        reasons = {u["reason"] for u in result.report.unsupported}
        assert "unsupported_phase" in reasons


class TestTcaWaterMultiComponentBlocksSkipped:
    """DOI 10.1016/j.tca.2018.04.018: the single-component Gas Cp block
    (water) yields exactly one payload; the two-component blocks in the
    same article are correctly recorded unsupported, not silently
    dropped."""

    DOI = "10.1016/j.tca.2018.04.018"

    def test_single_water_payload(self):
        result = _map_real_doi(self.DOI)
        assert len(result.payloads) == 1
        assert result.report.identity[0]["inchikey"] == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"

    def test_state_basis_real_gas_pressure_variable_sourced(self):
        result = _map_real_doi(self.DOI)
        payload = result.payloads[0]
        assert payload.state_basis.value == "real_gas"
        assert payload.raw_payload_json["thermoml"]["pressure_source"] == "variable"
        assert payload.pressure_bar == pytest.approx(30.0)  # 3000 kPa / 100.0

    def test_unsupported_reasons_include_multi_component_and_unsupported_phase(self):
        result = _map_real_doi(self.DOI)
        assert result.report.rejected == []
        reasons = {u["reason"] for u in result.report.unsupported}
        assert reasons == {"unsupported_phase", "multi_component"}
