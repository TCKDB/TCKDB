# ThermoML fixtures

All XML files in this directory are **hand-authored** test fixtures. None of
them was extracted from the NIST TRC ThermoML Archive (`mds2-2422`), and none
of them was copied from, or contains any numeric value taken from, any real
journal article.

`cp_gas_single_component.xml`, `cp_ideal_gas_no_pressure.xml`,
`cp_gas_missing_pressure.xml` and `cp_prediction_only.xml` are **structurally
modelled** on the shape of a real gas-phase flow-calorimetry heat-capacity
article — DOI `10.1016/j.fluid.2016.07.034` (fluoroethane,
`UHCBBWUQDAVSMS-UHFFFAOYSA-N`, 38 flow-calorimetry Cp values at
315.33-365.75 K and 101.325 kPa, phase tagged "Gas") — because that is the
pilot species/DOI named in the Phase C plan amendment. "Structurally
modelled" means: same compound identity, same phase tag, same measurement
method (`Flow calorimetry`), same general pressure/temperature range shape.
It does **not** mean any temperature, pressure, Cp value, uncertainty, or
digit count below was read from that article or from the ThermoML archive —
every number in these files was invented for the test suite.

`cp_liquid_unsupported.xml` and `cp_mixture_unsupported.xml` exercise phases
and component counts our mapper does not support (liquid phase, and a
two-component block) and are not modelled on any specific article.

`schema_invalid.xml` is deliberately malformed against the committed
`../schema/ThermoML.xsd` (an out-of-enumeration `ePropName` value and a
missing required `ePresentation` element) so `validate_bytes()` has something
real to reject.

Why hand-author instead of trimming a real archive file: the archive's terms
state "Files in the ThermoML Archives are available with permission of the
journal publishers" (see `TERMS_TEXT` in `../__init__.py`) — committing any
excerpt of a real article, even a truncated one, would need that permission,
which this repository does not have. Hand-authoring against the public XSD
avoids the question entirely while still exercising every code path the real
archive would.
