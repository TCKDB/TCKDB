# ThermoML fixtures

All XML files in this directory are **hand-authored** test fixtures. None of
them was extracted from the NIST TRC ThermoML Archive (`mds2-2422`), and none
of them was copied from, or contains any numeric value taken from, any real
journal article.

## Component/Compound keying

Every fixture except `cp_ideal_gas_no_pressure.xml` keys its `Component` and
`Compound` elements by `RegNum/nOrgNum` — this is how the real archive keys
every Cp block it contains (see `parser.py`'s module docstring; the XSD
offers `nCompIndex`/`RegNum` as a genuine `choice`, but the archive only
uses the latter). `cp_ideal_gas_no_pressure.xml` is the one deliberate
exception, kept keyed by `nCompIndex` so that join path still has direct
fixture coverage.

## The pilot article: benzene

The Phase C plan's pilot article (section C0/C2) is benzene,
`UHOVQNZJYSORNB-UHFFFAOYSA-N`, DOI `10.1016/j.jct.2013.08.022`: an "Ideal
gas" Cp block at a 100 kPa `Constraint`, 12 values 200-1000 K,
`Property-MethodID` carrying ONLY `sMethodName` "statistical
thermodynamics" (no `eMethodName`, no `Prediction`), and a combined
expanded uncertainty stating a level of confidence but no coverage factor.
`cp_ideal_gas_statistical_thermodynamics.xml` is **structurally modelled**
on this article's own Cp block — same compound identity, same phase, same
`sMethodName` string, same Constraint-sourced 100 kPa pressure, same
uncertainty shape. It does **not** mean any temperature, pressure, Cp
value, uncertainty, or digit count in that file was read from the article —
every number was invented for the test suite, and deliberately chosen to
NOT coincide with the article's own reported temperatures (200, 250,
273.15, 298.15, 300, 400, 500, 600, 700, 800, 900, 1000 K).

## The other real-archive-shaped fixtures: fluoroethane

`cp_gas_single_component.xml`, `cp_ideal_gas_no_pressure.xml`,
`cp_gas_missing_pressure.xml`, `cp_gas_variable_pressure.xml` and
`cp_prediction_only.xml` are **structurally modelled** on the shape of a
real gas-phase flow-calorimetry heat-capacity article — DOI
`10.1016/j.fluid.2016.07.034` (fluoroethane, `UHCBBWUQDAVSMS-UHFFFAOYSA-N`,
38 flow-calorimetry Cp values at 315.33-365.75 K, phase tagged "Gas") —
because that is a second real DOI this package's tests exercise. Its
pressure is **not** a fixed 101.325 kPa: the real article carries pressure
as a per-row `Variable` ranging 1020-3400 kPa, which
`cp_gas_variable_pressure.xml` specifically models (see its own docstring).
"Structurally modelled" means: same compound identity, same phase tag, same
measurement method (`Flow calorimetry`), same kind of pressure source
(Constraint in some fixtures to exercise that path, Variable in
`cp_gas_variable_pressure.xml` to exercise the other). It does **not** mean
any temperature, pressure, Cp value, uncertainty, or digit count was read
from that article or from the ThermoML archive — every number in these
files was invented for the test suite, and deliberately chosen to NOT
coincide with the article's own endpoint temperatures/pressures
(315.33/365.75 K, 1020/3400 kPa).

## Mutation-targeted fixtures

`cp_ambiguous_phase.xml`, `cp_unsupported_standard_state.xml`,
`cp_empty_uncertainty_value.xml`, `cp_unrecognized_smethodname.xml` and
`cp_uncertainty_precedence_order.xml` each exist to exercise one specific
parser/mapper rule (phase resolution, `eStandardState` rejection, an empty
per-value uncertainty entry, the observed-method-string allowlist, and
uncertainty precedence ordering, respectively) — see each file's own
docstring for exactly what it proves and which mutation it catches.

`cp_liquid_unsupported.xml` and `cp_mixture_unsupported.xml` exercise
phases and component counts our mapper does not support (liquid phase, and
a two-component block) and are not modelled on any specific article.

`schema_invalid.xml` is deliberately malformed against the committed
`../schema/ThermoML.xsd` (an out-of-enumeration `ePropName` value and a
missing required `ePresentation` element) so `validate_bytes()` has
something real to reject.

## Why hand-author instead of trimming a real archive file

The archive's terms state "The ThermoML files corresponding to articles in
the journals are available here with permission of the journal publishers"
(quoted verbatim as the second paragraph of `TERMS_TEXT` in
`../__init__.py`) — committing any excerpt of a real article, even a
truncated one, would need that permission, which this repository does not
have. Hand-authoring against the public XSD avoids the question entirely
while still exercising every code path the real archive would.

## Real-archive coverage without committing archive content

`tests/importers/thermoml/test_real_archive_smoke.py` is the one place
this package's tests touch real archive bytes — it is opt-in
(`TCKDB_THERMOML_ARCHIVE=<path to a verified ThermoML.v2020-09-30.tgz>`)
and SKIPS with a visible reason when that variable is unset, precisely so
CI and a normal local run never need the 189 MB archive on disk. It
asserts counts and state fields (payload counts, `state_basis`, pressure
source/range, uncertainty kind/coverage/confidence/assessor, origin,
method_note, unsupported/rejected reasons) against the four DOIs named in
this PR's body — never a specific Cp/temperature/pressure number, so no
real article's data point ever lives in version control.
