"""WP0 pilot scan script, driven against a synthetic archive this suite builds itself.

Never touches the real 189 MB NIST archive or the network. A single seven-member ``.tgz`` is
built in-process with hand-written, minimal ThermoML documents, each isolating one thing the
scanner must get right:

- ``methane`` -- single-component, Ideal gas Cp, ``eMethodName``, a per-point pressure
  ``Variable`` (not a ``Constraint``). The one record the strict scan is expected to find.
- ``ethane`` -- single-component, ``Liquid`` phase, ``eMethodName``. Must NOT match; the phase
  filter's only reason to exist in this fixture.
- ``mixture`` -- a two-component block reporting the target property at Ideal gas phase. Must be
  excluded (single-component requirement) AND counted in ``skipped_mixture_blocks``.
- ``prediction`` -- single-component, Ideal gas, a ``Prediction`` origin. Must be excluded AND
  counted in ``skipped_prediction``.
- ``smethod`` -- single-component, Ideal gas, an ``sMethodName``-only (free-text) origin. Must be
  excluded from the main candidate table AND counted in ``skipped_free_method`` AND surface in
  the separate ``free_method_ideal_gas_candidates`` report.
- ``phase_fallback`` -- single-component, Ideal gas, ``eMethodName``, with NO ``PropPhaseID`` on
  the ``Property`` at all: phase can only be read from the block-level ``PhaseID`` fallback. Must
  match.
- ``join_decoy`` -- single-component, Ideal gas, ``eMethodName``, sharing each ``NumValues`` with
  a second, decoy property (a fabricated "Mass density") that is listed *before* the target Cp
  property and carries a per-value uncertainty the Cp property does not. Must match with
  ``uncertainty_elements_present == []`` -- this is only true if the ``nPropNumber`` join
  actually selects the Cp ``PropertyValue`` and not the first one encountered. Also carries a
  block-level ``Constraint`` pressure (as opposed to methane's per-point ``Variable``).

No DB, no ``SessionLocal`` override needed: the script under test is stdlib-only and read-only.
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
MIXTURE_A_INCHIKEY = "QAAAAWERTYQZAA-UHFFFAOYSA-N"
MIXTURE_B_INCHIKEY = "QBBBBWERTYQZBB-UHFFFAOYSA-N"
PREDICTION_INCHIKEY = "QCCCCWERTYQZCC-UHFFFAOYSA-N"
SMETHOD_INCHIKEY = "QDDDDWERTYQZDD-UHFFFAOYSA-N"
PHASE_FALLBACK_INCHIKEY = "QEEEEWERTYQZEE-UHFFFAOYSA-N"
JOIN_INCHIKEY = "QFFFFWERTYQZFF-UHFFFAOYSA-N"
NO_KEY_ORG_NUM = 1

_NS = "http://www.iupac.org/namespaces/ThermoML"


def _tag(local: str) -> str:
    return f"{{{_NS}}}{local}"


# A single-component, ideal-gas Cp record with an eMethodName (enumerated, experimental), a
# combined expanded uncertainty tied to a property-level coverage factor and level of confidence,
# and a per-point pressure Variable (finding 2: pressure can be a Variable, not just a
# Constraint). This is the one record the strict scan is expected to find.
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
    <Variable>
      <nVarNumber>2</nVarNumber>
      <VariableID><VariableType><ePressure>Pressure, kPa</ePressure></VariableType></VariableID>
    </Variable>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>300</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
      <VariableValue><nVarNumber>2</nVarNumber><nVarValue>500</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
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
      <VariableValue><nVarNumber>2</nVarNumber><nVarValue>700</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
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

# A two-component ("mixture") block reporting the target property at Ideal gas phase with an
# eMethodName -- everything about it would otherwise qualify. The single-component requirement is
# the only reason to exclude it. Catches mutation (b): `len(components) != 1` weakened to
# `len(components) < 1`, which would let a 2-component block fall through unexcluded.
_MIXTURE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="{_NS}">
  <Version><nVersionMajor>2</nVersionMajor><nVersionMinor>0</nVersionMinor></Version>
  <Citation>
    <sAuthor>Test, C.</sAuthor>
    <sPubName>Test J.</sPubName>
    <yrPubYr>2019</yrPubYr>
    <sDOI>10.1000/test-mixture</sDOI>
  </Citation>
  <Compound>
    <RegNum><nOrgNum>1</nOrgNum></RegNum>
    <sStandardInChI>InChI=1S/test-A</sStandardInChI>
    <sStandardInChIKey>{MIXTURE_A_INCHIKEY}</sStandardInChIKey>
    <sCommonName>mixture-component-a</sCommonName>
  </Compound>
  <Compound>
    <RegNum><nOrgNum>2</nOrgNum></RegNum>
    <sStandardInChI>InChI=1S/test-B</sStandardInChI>
    <sStandardInChIKey>{MIXTURE_B_INCHIKEY}</sStandardInChIKey>
    <sCommonName>mixture-component-b</sCommonName>
  </Compound>
  <PureOrMixtureData>
    <nPureOrMixtureDataNumber>1</nPureOrMixtureDataNumber>
    <Component><RegNum><nOrgNum>1</nOrgNum></RegNum></Component>
    <Component><RegNum><nOrgNum>2</nOrgNum></RegNum></Component>
    <Property>
      <nPropNumber>1</nPropNumber>
      <Property-MethodID>
        <PropertyGroup>
          <HeatCapacityAndDerivedProp>
            <ePropName>Molar heat capacity at constant pressure, J/K/mol</ePropName>
            <eMethodName>Flow calorimetry</eMethodName>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID><ePropPhase>Ideal gas</ePropPhase></PropPhaseID>
    </Property>
    <PhaseID><ePhase>Ideal gas</ePhase></PhaseID>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID><VariableType><eTemperature>Temperature, K</eTemperature></VariableType></VariableID>
    </Variable>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>310</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>50.0</nPropValue>
        <nPropDigits>3</nPropDigits>
      </PropertyValue>
    </NumValues>
  </PureOrMixtureData>
</DataReport>
"""

# Single-component, Ideal gas, but the property's method choice is a `Prediction`, not an
# `eMethodName`. Catches mutation (e): a `Prediction` origin counted in `skipped_prediction` but
# not actually excluded from the candidate table.
_PREDICTION_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="{_NS}">
  <Version><nVersionMajor>2</nVersionMajor><nVersionMinor>0</nVersionMinor></Version>
  <Citation>
    <sAuthor>Test, D.</sAuthor>
    <sPubName>Test J.</sPubName>
    <yrPubYr>2018</yrPubYr>
    <sDOI>10.1000/test-prediction</sDOI>
  </Citation>
  <Compound>
    <RegNum><nOrgNum>1</nOrgNum></RegNum>
    <sStandardInChI>InChI=1S/test-pred</sStandardInChI>
    <sStandardInChIKey>{PREDICTION_INCHIKEY}</sStandardInChIKey>
    <sCommonName>prediction-compound</sCommonName>
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
            <Prediction><ePredictionType>Group contribution</ePredictionType></Prediction>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID><ePropPhase>Ideal gas</ePropPhase></PropPhaseID>
    </Property>
    <PhaseID><ePhase>Ideal gas</ePhase></PhaseID>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID><VariableType><eTemperature>Temperature, K</eTemperature></VariableType></VariableID>
    </Variable>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>320</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>42.0</nPropValue>
        <nPropDigits>3</nPropDigits>
      </PropertyValue>
    </NumValues>
  </PureOrMixtureData>
</DataReport>
"""

# Single-component, Ideal gas, an sMethodName (free-text) method -- experimental in the schema's
# sense but explicitly excluded from the strict eMethodName-only candidate table per the WP0
# brief. Catches mutation (f): counted in `skipped_free_method` but not actually excluded from
# `candidates` / `playground_hit`, and verifies it DOES surface in the separate
# `free_method_ideal_gas_candidates` report (finding 4).
_SMETHOD_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="{_NS}">
  <Version><nVersionMajor>2</nVersionMajor><nVersionMinor>0</nVersionMinor></Version>
  <Citation>
    <sAuthor>Test, E.</sAuthor>
    <sPubName>Test J.</sPubName>
    <yrPubYr>2017</yrPubYr>
    <sDOI>10.1000/test-smethod</sDOI>
  </Citation>
  <Compound>
    <RegNum><nOrgNum>1</nOrgNum></RegNum>
    <sStandardInChI>InChI=1S/test-smethod</sStandardInChI>
    <sStandardInChIKey>{SMETHOD_INCHIKEY}</sStandardInChIKey>
    <sCommonName>smethod-compound</sCommonName>
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
            <sMethodName>Statistical thermodynamics</sMethodName>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID><ePropPhase>Ideal gas</ePropPhase></PropPhaseID>
    </Property>
    <PhaseID><ePhase>Ideal gas</ePhase></PhaseID>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID><VariableType><eTemperature>Temperature, K</eTemperature></VariableType></VariableID>
    </Variable>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>298.15</nVarValue><nVarDigits>5</nVarDigits></VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>60.0</nPropValue>
        <nPropDigits>3</nPropDigits>
      </PropertyValue>
    </NumValues>
  </PureOrMixtureData>
</DataReport>
"""

# Single-component, Ideal gas, an eMethodName -- but the Property carries NO PropPhaseID at all.
# Phase can only be resolved via the block-level PhaseID fallback. Catches mutation (i): the
# block-level PhaseID fallback removed from `_property_phase`, which would make this property's
# phase unresolvable (None) and drop it from the candidate table entirely.
_PHASE_FALLBACK_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="{_NS}">
  <Version><nVersionMajor>2</nVersionMajor><nVersionMinor>0</nVersionMinor></Version>
  <Citation>
    <sAuthor>Test, F.</sAuthor>
    <sPubName>Test J.</sPubName>
    <yrPubYr>2016</yrPubYr>
    <sDOI>10.1000/test-phase-fallback</sDOI>
  </Citation>
  <Compound>
    <RegNum><nOrgNum>1</nOrgNum></RegNum>
    <sStandardInChI>InChI=1S/test-phase-fallback</sStandardInChI>
    <sStandardInChIKey>{PHASE_FALLBACK_INCHIKEY}</sStandardInChIKey>
    <sCommonName>phase-fallback-compound</sCommonName>
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
            <eMethodName>Adiabatic calorimetry</eMethodName>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
    </Property>
    <PhaseID><ePhase>Ideal gas</ePhase></PhaseID>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID><VariableType><eTemperature>Temperature, K</eTemperature></VariableType></VariableID>
    </Variable>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>330</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>45.0</nPropValue>
        <nPropDigits>3</nPropDigits>
      </PropertyValue>
    </NumValues>
  </PureOrMixtureData>
</DataReport>
"""

# Single-component, Ideal gas, an eMethodName -- but each NumValues carries TWO PropertyValue
# entries: a decoy "density" property (nPropNumber 1, listed FIRST, carrying a per-value
# uncertainty) and the target Cp property (nPropNumber 2, listed second, with NO per-value
# uncertainty). Also uses a block-level Constraint pressure (methane above uses a Variable) so
# both pressure kinds get exercised. Catches mutation (h): the `nPropNumber` equality join in the
# NumValues/PropertyValue lookup mutated to always match, which would pick the decoy's PropertyValue
# (first in document order) instead of the Cp one -- observable because the decoy carries an
# uncertainty tag the real Cp entry does not.
_JOIN_DECOY_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="{_NS}">
  <Version><nVersionMajor>2</nVersionMajor><nVersionMinor>0</nVersionMinor></Version>
  <Citation>
    <sAuthor>Test, G.</sAuthor>
    <sPubName>Test J.</sPubName>
    <yrPubYr>2015</yrPubYr>
    <sDOI>10.1000/test-join-decoy</sDOI>
  </Citation>
  <Compound>
    <RegNum><nOrgNum>1</nOrgNum></RegNum>
    <sStandardInChI>InChI=1S/test-join-decoy</sStandardInChI>
    <sStandardInChIKey>{JOIN_INCHIKEY}</sStandardInChIKey>
    <sCommonName>join-decoy-compound</sCommonName>
  </Compound>
  <PureOrMixtureData>
    <nPureOrMixtureDataNumber>1</nPureOrMixtureDataNumber>
    <Component><RegNum><nOrgNum>1</nOrgNum></RegNum></Component>
    <Property>
      <nPropNumber>1</nPropNumber>
      <Property-MethodID>
        <PropertyGroup>
          <MassDensityAndRelatedProp>
            <ePropName>Mass density, kg/m3</ePropName>
            <eMethodName>Vibrating tube densitometry</eMethodName>
          </MassDensityAndRelatedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID><ePropPhase>Ideal gas</ePropPhase></PropPhaseID>
    </Property>
    <Property>
      <nPropNumber>2</nPropNumber>
      <Property-MethodID>
        <PropertyGroup>
          <HeatCapacityAndDerivedProp>
            <ePropName>Molar heat capacity at constant pressure, J/K/mol</ePropName>
            <eMethodName>Flow calorimetry</eMethodName>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID><ePropPhase>Ideal gas</ePropPhase></PropPhaseID>
    </Property>
    <PhaseID><ePhase>Ideal gas</ePhase></PhaseID>
    <Constraint>
      <ConstraintID><ConstraintType><ePressure>Pressure, kPa</ePressure></ConstraintType></ConstraintID>
      <nConstraintValue>100</nConstraintValue>
    </Constraint>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID><VariableType><eTemperature>Temperature, K</eTemperature></VariableType></VariableID>
    </Variable>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>340</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>1.5</nPropValue>
        <nPropDigits>3</nPropDigits>
        <PropUncertainty>
          <nUncertAssessNum>1</nUncertAssessNum>
          <nStdUncertValue>0.1</nStdUncertValue>
        </PropUncertainty>
      </PropertyValue>
      <PropertyValue>
        <nPropNumber>2</nPropNumber>
        <nPropValue>55.0</nPropValue>
        <nPropDigits>3</nPropDigits>
      </PropertyValue>
    </NumValues>
  </PureOrMixtureData>
</DataReport>
"""

# A compound with sStandardInChI but NO sStandardInChIKey element at all, matched by a qualifying
# (single-component, target property, Ideal gas, eMethodName) block. Before finding 6's fix this
# was silently dropped with no trace; now it must be counted in `compounds_without_inchikey` and
# must not appear in `candidates` or `free_method_ideal_gas_candidates` (it has no key to key by).
_NO_INCHIKEY_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="{_NS}">
  <Version><nVersionMajor>2</nVersionMajor><nVersionMinor>0</nVersionMinor></Version>
  <Citation>
    <sAuthor>Test, H.</sAuthor>
    <sPubName>Test J.</sPubName>
    <yrPubYr>2014</yrPubYr>
    <sDOI>10.1000/test-no-inchikey</sDOI>
  </Citation>
  <Compound>
    <RegNum><nOrgNum>{NO_KEY_ORG_NUM}</nOrgNum></RegNum>
    <sStandardInChI>InChI=1S/test-no-inchikey</sStandardInChI>
    <sCommonName>no-inchikey-compound</sCommonName>
  </Compound>
  <PureOrMixtureData>
    <nPureOrMixtureDataNumber>1</nPureOrMixtureDataNumber>
    <Component><RegNum><nOrgNum>{NO_KEY_ORG_NUM}</nOrgNum></RegNum></Component>
    <Property>
      <nPropNumber>1</nPropNumber>
      <Property-MethodID>
        <PropertyGroup>
          <HeatCapacityAndDerivedProp>
            <ePropName>Molar heat capacity at constant pressure, J/K/mol</ePropName>
            <eMethodName>Flow calorimetry</eMethodName>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID><ePropPhase>Ideal gas</ePropPhase></PropPhaseID>
    </Property>
    <PhaseID><ePhase>Ideal gas</ePhase></PhaseID>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID><VariableType><eTemperature>Temperature, K</eTemperature></VariableType></VariableID>
    </Variable>
    <NumValues>
      <VariableValue><nVarNumber>1</nVarNumber><nVarValue>310</nVarValue><nVarDigits>3</nVarDigits></VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>48.0</nPropValue>
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
    documents = {
        "10.1000/test-methane-ideal": _METHANE_IDEAL_GAS_XML,
        "10.1000/test-ethane-liquid": _ETHANE_LIQUID_XML,
        "10.1000/test-mixture": _MIXTURE_XML,
        "10.1000/test-prediction": _PREDICTION_XML,
        "10.1000/test-smethod": _SMETHOD_XML,
        "10.1000/test-phase-fallback": _PHASE_FALLBACK_XML,
        "10.1000/test-join-decoy": _JOIN_DECOY_XML,
    }
    with tarfile.open(archive_path, "w:gz") as tf:
        for stem, xml in documents.items():
            _add_member(tf, f"{stem}.xml", xml.encode("utf-8"))
            _add_member(tf, f"{stem}.json", b'{"stub": true}')
    return archive_path


@pytest.fixture()
def no_inchikey_archive(tmp_path: pathlib.Path) -> pathlib.Path:
    archive_path = tmp_path / "ThermoML.no_inchikey.tgz"
    with tarfile.open(archive_path, "w:gz") as tf:
        _add_member(tf, "10.1000/test-no-inchikey.xml", _NO_INCHIKEY_XML.encode("utf-8"))
        _add_member(tf, "10.1000/test-no-inchikey.json", b'{"stub": true}')
    return archive_path


@pytest.fixture()
def inchikeys_file(tmp_path: pathlib.Path) -> pathlib.Path:
    # Deliberately includes the mixture, Prediction and sMethodName-only compounds too (not just
    # the three that should genuinely match): each is a distinct, valid InChIKey, so if any of
    # them leaked past its exclusion (mutations b, e, f) it would inflate `playground_hit` and
    # appear in `candidates`, not just silently fail to appear anywhere.
    path = tmp_path / "playground.txt"
    path.write_text(
        f"{METHANE_INCHIKEY} methane\n"
        f"{ETHANE_INCHIKEY} ethane\n"
        f"{MIXTURE_A_INCHIKEY} mixture-component-a\n"
        f"{MIXTURE_B_INCHIKEY} mixture-component-b\n"
        f"{PREDICTION_INCHIKEY} prediction-compound\n"
        f"{SMETHOD_INCHIKEY} smethod-compound\n"
        f"{PHASE_FALLBACK_INCHIKEY} phase-fallback-compound\n"
        f"{JOIN_INCHIKEY} join-decoy-compound\n"
    )
    return path


def _run(scan_module, monkeypatch, archive, inchikeys, out, *, digest_override: str | None) -> int:
    if digest_override is not None:
        monkeypatch.setenv("THERMOML_PILOT_SCAN_ARCHIVE_SHA256", digest_override)
    else:
        monkeypatch.delenv("THERMOML_PILOT_SCAN_ARCHIVE_SHA256", raising=False)
    return scan_module.main(["--archive", str(archive), "--inchikeys", str(inchikeys), "--out", str(out)])


class TestFindsOnlyTheQualifyingRecords:
    """Catches: dropped single-component check, dropped eMethodName-vs-Prediction check, a
    changed property-string comparison, the phase filter accepting ``"Liquid"``, the
    single-component check weakened to ``< 1`` (mutation b), a ``Prediction`` counted but not
    excluded (mutation e), an ``sMethodName`` match counted but not excluded (mutation f), the
    block-level ``PhaseID`` fallback removed (mutation i), and the ``nPropNumber`` join mutated to
    always match (mutation h). Any of those would change which three compounds are reported.
    """

    def test_exactly_the_three_qualifying_candidates_are_reported(
        self, scan_module, monkeypatch, synthetic_archive, inchikeys_file, tmp_path
    ):
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(synthetic_archive)
        exit_code = _run(scan_module, monkeypatch, synthetic_archive, inchikeys_file, out, digest_override=digest)

        assert exit_code == scan_module.EXIT_OK

        payload = json.loads(out.read_text())
        assert payload["counts"]["playground_hit"] == 3
        assert payload["counts"]["playground_hit_inchikeys"] == sorted(
            [METHANE_INCHIKEY, PHASE_FALLBACK_INCHIKEY, JOIN_INCHIKEY]
        )
        assert len(payload["candidates"]) == 3
        candidates_by_key = {c["inchikey"]: c for c in payload["candidates"]}

        # Mutation (b) and the phase filter: neither the ethane (wrong phase) nor either mixture
        # component appears anywhere in the candidate table.
        assert ETHANE_INCHIKEY not in candidates_by_key
        assert MIXTURE_A_INCHIKEY not in candidates_by_key
        assert MIXTURE_B_INCHIKEY not in candidates_by_key
        # Mutation (e) and (f): neither the Prediction nor the sMethodName-only compound leaked in.
        assert PREDICTION_INCHIKEY not in candidates_by_key
        assert SMETHOD_INCHIKEY not in candidates_by_key

        methane = candidates_by_key[METHANE_INCHIKEY]
        assert methane["e_phase"] == "Ideal gas"
        assert methane["doi"] == "10.1000/test-methane-ideal"

        # Mutation (i): phase-fallback compound has no PropPhaseID; only the block-level PhaseID
        # fallback lets it resolve to "Ideal gas" and appear here at all.
        phase_fallback = candidates_by_key[PHASE_FALLBACK_INCHIKEY]
        assert phase_fallback["e_phase"] == "Ideal gas"

        # Mutation (h): the decoy density PropertyValue (nPropNumber 1, listed first, carrying an
        # uncertainty) must NOT be picked up in place of the real Cp PropertyValue (nPropNumber 2,
        # no uncertainty). If the nPropNumber join were mutated to always match, this would be
        # ["nStdUncertValue"] instead.
        join_decoy = candidates_by_key[JOIN_INCHIKEY]
        assert join_decoy["uncertainty_elements_present"] == []
        assert join_decoy["n_values"] == 1

    def test_method_and_uncertainty_fields_are_reported(
        self, scan_module, monkeypatch, synthetic_archive, inchikeys_file, tmp_path
    ):
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(synthetic_archive)
        _run(scan_module, monkeypatch, synthetic_archive, inchikeys_file, out, digest_override=digest)

        candidates_by_key = {c["inchikey"]: c for c in json.loads(out.read_text())["candidates"]}
        methane = candidates_by_key[METHANE_INCHIKEY]
        assert methane["e_method_name"] == "Derived from speed of sound"
        assert methane["origin"] == "experimental_enum"
        assert methane["n_values"] == 2
        assert methane["temperature_range_k"] == [300.0, 350.0]
        assert methane["uncertainty_elements_present"] == ["nCombExpandUncertValue"]
        assert methane["has_coverage_factor"] is True
        assert methane["has_level_of_confidence"] is True


class TestPressureReading:
    """Finding 2: pressure must be read from a per-point ``Variable`` as well as a block-level
    ``Constraint``, and the kind/min/max/distinct-count must be reported per candidate.
    """

    def test_variable_pressure_is_read_with_kind_and_distinct_count(
        self, scan_module, monkeypatch, synthetic_archive, inchikeys_file, tmp_path
    ):
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(synthetic_archive)
        _run(scan_module, monkeypatch, synthetic_archive, inchikeys_file, out, digest_override=digest)

        candidates_by_key = {c["inchikey"]: c for c in json.loads(out.read_text())["candidates"]}
        methane = candidates_by_key[METHANE_INCHIKEY]
        assert methane["pressure_kind"] == "variable"
        assert methane["pressures_kpa"] == [500.0, 700.0]
        assert methane["pressure_min_kpa"] == 500.0
        assert methane["pressure_max_kpa"] == 700.0
        assert methane["pressure_n_distinct"] == 2

    def test_constraint_pressure_is_still_read(
        self, scan_module, monkeypatch, synthetic_archive, inchikeys_file, tmp_path
    ):
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(synthetic_archive)
        _run(scan_module, monkeypatch, synthetic_archive, inchikeys_file, out, digest_override=digest)

        candidates_by_key = {c["inchikey"]: c for c in json.loads(out.read_text())["candidates"]}
        join_decoy = candidates_by_key[JOIN_INCHIKEY]
        assert join_decoy["pressure_kind"] == "constraint"
        assert join_decoy["pressures_kpa"] == [100.0]
        assert join_decoy["pressure_n_distinct"] == 1


class TestMixturePredictionAndFreeMethodAreCountedNotJustExcluded:
    """Finding 3: the mixture block, the Prediction block and the sMethodName-only block must
    each be both excluded from the strict candidate table AND counted in their own counter.
    """

    def test_counters_reflect_each_excluded_block(
        self, scan_module, monkeypatch, synthetic_archive, inchikeys_file, tmp_path
    ):
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(synthetic_archive)
        _run(scan_module, monkeypatch, synthetic_archive, inchikeys_file, out, digest_override=digest)

        counts = json.loads(out.read_text())["counts"]
        assert counts["skipped_mixture_blocks"] == 1
        assert counts["skipped_prediction"] == 1
        assert counts["skipped_free_method"] == 1


class TestFreeMethodIdealGasReport:
    """Finding 4: sMethodName-only matches must survive even though they are excluded from the
    strict candidate table, and must appear in the dedicated report with full detail.
    """

    def test_smethod_compound_surfaces_in_the_dedicated_report(
        self, scan_module, monkeypatch, synthetic_archive, inchikeys_file, tmp_path
    ):
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(synthetic_archive)
        _run(scan_module, monkeypatch, synthetic_archive, inchikeys_file, out, digest_override=digest)

        payload = json.loads(out.read_text())
        free_by_key = {c["inchikey"]: c for c in payload["free_method_ideal_gas_candidates"]}
        assert SMETHOD_INCHIKEY in free_by_key
        smethod = free_by_key[SMETHOD_INCHIKEY]
        assert smethod["e_method_name"] == "Statistical thermodynamics"
        assert smethod["origin"] == "experimental_free"
        assert smethod["e_phase"] == "Ideal gas"
        assert smethod["compound_name"] == "smethod-compound"
        assert smethod["doi"] == "10.1000/test-smethod"
        assert smethod["n_values"] == 1

        # Never in the strict candidate table, regardless of playground status.
        assert SMETHOD_INCHIKEY not in {c["inchikey"] for c in payload["candidates"]}


class TestCompoundsWithoutInchikey:
    """Finding 6: a compound with sStandardInChI but no sStandardInChIKey must be counted and
    logged, not silently dropped."""

    def test_missing_inchikey_is_counted_and_logged(self, scan_module, monkeypatch, no_inchikey_archive, tmp_path):
        inchikeys = tmp_path / "playground.txt"
        inchikeys.write_text(f"{METHANE_INCHIKEY} methane\n")
        out = tmp_path / "result.json"
        digest = scan_module.sha256_file(no_inchikey_archive)

        exit_code = _run(scan_module, monkeypatch, no_inchikey_archive, inchikeys, out, digest_override=digest)

        # No playground hit in this tiny archive -- exit 1, but output must still be written.
        assert exit_code == scan_module.EXIT_NO_PLAYGROUND_CANDIDATE
        payload = json.loads(out.read_text())
        assert payload["counts"]["compounds_without_inchikey"] == 1
        assert len(payload["compounds_without_inchikey"]) == 1
        logged = payload["compounds_without_inchikey"][0]
        assert logged["doi"] == "10.1000/test-no-inchikey"
        assert logged["compound_name"] == "no-inchikey-compound"
        # It cannot appear anywhere it would need a key to be identified by.
        assert payload["candidates"] == []
        assert payload["free_method_ideal_gas_candidates"] == []


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


class TestArchiveNotFound:
    """Finding 7: a missing --archive path is a distinct exit code (3) from a digest mismatch
    (2); previously both returned 2."""

    def test_missing_archive_exits_three(self, scan_module, monkeypatch, inchikeys_file, tmp_path):
        missing = tmp_path / "does-not-exist.tgz"
        out = tmp_path / "result.json"
        monkeypatch.delenv("THERMOML_PILOT_SCAN_ARCHIVE_SHA256", raising=False)

        exit_code = scan_module.main(["--archive", str(missing), "--inchikeys", str(inchikeys_file), "--out", str(out)])

        assert exit_code == scan_module.EXIT_ARCHIVE_NOT_FOUND
        assert exit_code != scan_module.EXIT_DIGEST_MISMATCH
        assert not out.exists()


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
        # The sMethodName-only report is unaffected by playground status (finding 4): it still
        # reports the smethod compound even though nothing in the playground list was hit.
        assert SMETHOD_INCHIKEY in {c["inchikey"] for c in payload["free_method_ideal_gas_candidates"]}
