"""WP0 pilot scan script, driven against a synthetic archive this suite builds itself.

Never touches the real 189 MB NIST archive or the network. A tiny two-member ``.tgz`` is
built in-process with two hand-written, minimal ThermoML documents: one a single-component,
ideal-gas Cp record for methane with an ``eMethodName`` (matches), the other a single-component
Cp record for ethane at ``Liquid`` phase with an ``eMethodName`` too (must NOT match -- it is
the phase filter's only reason to exist in this fixture). No DB, no ``SessionLocal`` override
needed: the script under test is stdlib-only and read-only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import tarfile

import pytest

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "validation" / "thermoml_cp_pilot_scan.py"

METHANE_INCHIKEY = "VNWKTOKETHGBQD-UHFFFAOYSA-N"
ETHANE_INCHIKEY = "OTMSDBZUPAUEDD-UHFFFAOYSA-N"

_NS = "http://www.iupac.org/namespaces/ThermoML"

# A single-component, ideal-gas Cp record with an eMethodName (enumerated, experimental) and a
# combined expanded uncertainty tied to a property-level coverage factor and level of confidence.
# This is the one record the scan is expected to find.
_METHANE_IDEAL_GAS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="{_NS}">
  <Version><nVersionMajor>2</nVersionMajor><nVersionMinor>0</nVersionMinor></Version>
  <Citation>
    <sAuthor>Test, A.</sAuthor>
    <sPubName>Test J.</sPubName>
    <yrPubYr>2020</yrPubYr>
    <sDOI>10.1000/test-methane-ideal</sDOI>
  </Citation>
  <Compound>
    <RegNum><nOrgNum>1</nOrgNum></RegNum>
    <sStandardInChI>InChI=1S/CH4/h1H4</sStandardInChI>
    <sStandardInChIKey>{METHANE_INCHIKEY}</sStandardInChIKey>
    <sCommonName>methane</sCommonName>
  </Compound>
  <PureOrMixtureData>
    <nPureOrMixtureDataNumber>1</nPureOrMixtureDataNumber>
    <Component><RegNum><nOrgNum>1</nOrgNum></RegNum></Component>
    <Property>
      <nPropNumber>1</nPropNumber>
      <Property-MethodID>
        <PropertyGroup>
          <HeatCapacityAndDerivedProp>
            <ePropName>Molar heat capacity at constant pressure, J/K/mol</ePropName>
            <eMethodName>Derived from speed of sound</eMethodName>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID><ePropPhase>Ideal gas</ePropPhase></PropPhaseID>
      <CombinedUncertainty>
        <nCombUncertAssessNum>1</nCombUncertAssessNum>
        <eCombUncertEvalMethod>Propagation of evaluated standard uncertainties</eCombUncertEvalMethod>
        <nCombCoverageFactor>2</nCombCoverageFactor>
        <nCombUncertLevOfConfid>95</nCombUncertLevOfConfid>
      </CombinedUncertainty>
    </Property>
    <PhaseID><ePhase>Ideal gas</ePhase></PhaseID>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID><VariableType><eTemperature>Temperature, K</eTemperature></VariableType></VariableID>
    </Variable>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>300</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>35.7</nPropValue>
        <nPropDigits>3</nPropDigits>
        <CombinedUncertainty>
          <nCombUncertAssessNum>1</nCombUncertAssessNum>
          <nCombExpandUncertValue>0.5</nCombExpandUncertValue>
        </CombinedUncertainty>
      </PropertyValue>
    </NumValues>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>350</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>36.7</nPropValue>
        <nPropDigits>3</nPropDigits>
        <CombinedUncertainty>
          <nCombUncertAssessNum>1</nCombUncertAssessNum>
          <nCombExpandUncertValue>0.5</nCombExpandUncertValue>
        </CombinedUncertainty>
      </PropertyValue>
    </NumValues>
  </PureOrMixtureData>
</DataReport>
"""

# A single-component Liquid-phase Cp record, also with an eMethodName. Same property string,
# same "experimental, not a Prediction" origin as the methane record above -- the ONLY thing
# that should exclude it is the phase. If the phase filter were loosened to accept "Liquid" (the
# mutation this fixture exists to catch), this record would wrongly appear as a second candidate.
_ETHANE_LIQUID_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="{_NS}">
  <Version><nVersionMajor>2</nVersionMajor><nVersionMinor>0</nVersionMinor></Version>
  <Citation>
    <sAuthor>Test, B.</sAuthor>
    <sPubName>Test J.</sPubName>
    <yrPubYr>2021</yrPubYr>
    <sDOI>10.1000/test-ethane-liquid</sDOI>
  </Citation>
  <Compound>
    <RegNum><nOrgNum>1</nOrgNum></RegNum>
    <sStandardInChI>InChI=1S/C2H6/c1-2/h1-2H3</sStandardInChI>
    <sStandardInChIKey>{ETHANE_INCHIKEY}</sStandardInChIKey>
    <sCommonName>ethane</sCommonName>
  </Compound>
  <PureOrMixtureData>
    <nPureOrMixtureDataNumber>1</nPureOrMixtureDataNumber>
    <Component><RegNum><nOrgNum>1</nOrgNum></RegNum></Component>
    <Property>
      <nPropNumber>1</nPropNumber>
      <Property-MethodID>
        <PropertyGroup>
          <HeatCapacityAndDerivedProp>
            <ePropName>Molar heat capacity at constant pressure, J/K/mol</ePropName>
            <eMethodName>Vacuum adiabatic calorimetry</eMethodName>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID><ePropPhase>Liquid</ePropPhase></PropPhaseID>
    </Property>
    <PhaseID><ePhase>Liquid</ePhase></PhaseID>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID><VariableType><eTemperature>Temperature, K</eTemperature></VariableType></VariableID>
    </Variable>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>200</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>90.0</nPropValue>
        <nPropDigits>3</nPropDigits>
      </PropertyValue>
    </NumValues>
  </PureOrMixtureData>
</DataReport>
"""


@pytest.fixture(scope="module")
def scan_module():
    spec = importlib.util.spec_from_file_location("thermoml_cp_pilot_scan", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _add_member(tf: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    tf.addfile(info, io.BytesIO(content))


@pytest.fixture()
def synthetic_archive(tmp_path: pathlib.Path) -> pathlib.Path:
    archive_path = tmp_path / "ThermoML.synthetic.tgz"
    with tarfile.open(archive_path, "w:gz") as tf:
        _add_member(tf, "10.1000/test-methane-ideal.xml", _METHANE_IDEAL_GAS_XML.encode("utf-8"))
        _add_member(tf, "10.1000/test-methane-ideal.json", b'{"stub": true}')
        _add_member(tf, "10.1000/test-ethane-liquid.xml", _ETHANE_LIQUID_XML.encode("utf-8"))
        _add_member(tf, "10.1000/test-ethane-liquid.json", b'{"stub": true}')
    return archive_path


@pytest.fixture()
def inchikeys_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "playground.txt"
    path.write_text(f"{METHANE_INCHIKEY} methane\n{ETHANE_INCHIKEY} ethane\n")
    return path


def _run(scan_module, monkeypatch, archive, inchikeys, out, *, digest_override: str | None) -> int:
    if digest_override is not None:
        monkeypatch.setenv("THERMOML_PILOT_SCAN_ARCHIVE_SHA256", digest_override)
    else:
        monkeypatch.delenv("THERMOML_PILOT_SCAN_ARCHIVE_SHA256", raising=False)
    return scan_module.main(["--archive", str(archive), "--inchikeys", str(inchikeys), "--out", str(out)])


class TestFindsOnlyTheMatchingRecord:
    """Catches: dropped single-component check, dropped eMethodName-vs-Prediction check, a
    changed property-string comparison, and -- the mutation named in the brief -- the phase
    filter accepting ``"Liquid"``. Any of those would let the ethane fixture through as a
    second candidate, or (property-string / single-component mutations) drop methane too.
    """

    def test_exactly_one_candidate_is_reported(
        self, scan_module, monkeypatch, synthetic_archive, inchikeys_file, tmp_path
    ):
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(synthetic_archive)
        exit_code = _run(scan_module, monkeypatch, synthetic_archive, inchikeys_file, out, digest_override=digest)

        assert exit_code == scan_module.EXIT_OK

        payload = json.loads(out.read_text())
        assert payload["counts"]["playground_hit"] == 1
        assert payload["counts"]["playground_hit_inchikeys"] == [METHANE_INCHIKEY]
        assert len(payload["candidates"]) == 1

        candidate = payload["candidates"][0]
        assert candidate["inchikey"] == METHANE_INCHIKEY
        assert candidate["e_phase"] == "Ideal gas"
        assert candidate["doi"] == "10.1000/test-methane-ideal"

        # The mutation this fixture exists to catch: if the phase filter were loosened to
        # accept "Liquid", ethane would appear here too.
        assert ETHANE_INCHIKEY not in payload["counts"]["playground_hit_inchikeys"]
        assert all(c["inchikey"] != ETHANE_INCHIKEY for c in payload["candidates"])

    def test_method_and_uncertainty_fields_are_reported(
        self, scan_module, monkeypatch, synthetic_archive, inchikeys_file, tmp_path
    ):
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(synthetic_archive)
        _run(scan_module, monkeypatch, synthetic_archive, inchikeys_file, out, digest_override=digest)

        candidate = json.loads(out.read_text())["candidates"][0]
        assert candidate["e_method_name"] == "Derived from speed of sound"
        assert candidate["n_values"] == 2
        assert candidate["temperature_range_k"] == [300.0, 350.0]
        assert candidate["uncertainty_elements_present"] == ["nCombExpandUncertValue"]
        assert candidate["has_coverage_factor"] is True
        assert candidate["has_level_of_confidence"] is True


class TestDigestGate:
    """Catches: a script that reads archive members before checking the digest, or one that
    checks the digest against a hardcoded value instead of the (test-overridable) pin.
    """

    def test_wrong_pinned_digest_refuses_before_reading_anything(
        self, scan_module, monkeypatch, synthetic_archive, inchikeys_file, tmp_path
    ):
        out = tmp_path / "result.json"
        wrong_digest = "0" * 64
        exit_code = _run(scan_module, monkeypatch, synthetic_archive, inchikeys_file, out, digest_override=wrong_digest)

        assert exit_code == scan_module.EXIT_DIGEST_MISMATCH
        assert not out.exists(), "a digest mismatch must not write any output"

    def test_correct_digest_override_matches_recomputed_hash(self, scan_module, synthetic_archive):
        # Sanity check on the helper itself: hashlib and the script's own sha256_file agree.
        assert scan_module.sha256_file(synthetic_archive) == hashlib.sha256(synthetic_archive.read_bytes()).hexdigest()


class TestNoPlaygroundHitExitsOne:
    def test_exit_one_when_inchikeys_file_matches_nothing(self, scan_module, monkeypatch, synthetic_archive, tmp_path):
        inchikeys = tmp_path / "no_matches.txt"
        inchikeys.write_text("AAAAAAAAAAAAAA-BBBBBBBBBB-N nonexistent\n")
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(synthetic_archive)

        exit_code = _run(scan_module, monkeypatch, synthetic_archive, inchikeys, out, digest_override=digest)

        assert exit_code == scan_module.EXIT_NO_PLAYGROUND_CANDIDATE
        payload = json.loads(out.read_text())
        assert payload["candidates"] == []
        # The archive-wide fallback picture is still reported even with zero playground hits.
        assert payload["fallback_coverage_by_compound"]
        assert payload["fallback_coverage_by_compound"][0]["inchikey"] == METHANE_INCHIKEY
