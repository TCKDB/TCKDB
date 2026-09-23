# Unit Handling Policy

## Philosophy

Scientific databases must enforce unit consistency at the schema level, not at application convention. TCKDB uses three tiers, applied in order of preference.

## Tier 1: Fixed-unit columns (preferred)

Encode the unit in the column name. No companion unit field.

Use when the quantity has a natural canonical unit and cross-row comparisons depend on uniformity.

```
electronic_energy_hartree   -- energies from ESS output
ea_kj_mol                   -- activation energy
sigma_angstrom              -- Lennard-Jones sigma
h298_kj_mol                 -- standard enthalpy
temperature_k, tmin_k       -- temperatures
pressure_bar, pmin_bar      -- pressures
```

This covers the vast majority of columns. If there is a single obvious unit for the domain, use this tier.

## Enthalpy reference declaration (2026-09-23)

`thermo.enthalpy_reference_kind = formation_298k` declares
standard enthalpy of formation at 298.15 K: one mole of the species formed
from elements in their reference forms, whose formation enthalpies are zero.
At another temperature, H(T) is that formation energy plus the species' own
enthalpy increment from 298.15 K. The elemental term remains pinned at
298.15 K; it is not recomputed against the elements at T.

The declaration covers `h298_kj_mol`, `thermo_point.h_kj_mol`, Wilhoit
`h0_kj_mol`, and the NASA-7/NASA-9 enthalpy integration constants. A point's
`g_kj_mol` means H(T) - T*S(T), on the same reference zero, with entropy
converted to kJ/(mol*K). It is not a formation Gibbs energy recomputed
against elemental entropies. `enthalpy_formation_0k_kj_mol` retains its
separate, existing meaning.

The two-layer rule deliberately is not a scalar iff constraint:

- The database CHECK is `h298_kj_mol IS NULL OR enthalpy_reference_kind IS NOT NULL`.
- Every deposit workflow requires the declaration for any h298 scalar, point
  enthalpy, Wilhoit h0, or NASA-7/NASA-9 block, and refuses a declaration when
  none of that content exists. Cp/entropy-only deposits leave it null.
- Declared fit-only and point-only records are valid without h298. No scalar
  is evaluated from a fit and stored as though the depositor supplied it.

Absence is absence: null means the source did not declare the reference.
`EnthalpyReferenceKind` has exactly one member and no `unspecified` member.
No default depends on origin, software, magnitude, or another row. Legacy
rows are not backfilled, including approved immutable rows. The migration
adds the CHECK as `NOT VALID`, preserving legacy nulls while enforcing new
inserts and updates. It must not later be validated by inferring references
or by using the accepted-science repair mechanism.

Sensible increments such as H(T)-H(0) and absolute quantum-chemistry
enthalpies belong in `molecular_property_observation`, with their stated
property label, state, temperature, pressure and uncertainty meaning.
CCCBDB's explicitly labelled H(298.15)-H(0) is routed to an observation
payload with its source datum and identity hint intact. ARC requires an
explicit adapter configuration; its output does not establish a basis.

## Tier 2: Enum-constrained unit columns

Pair a value column with a unit enum when dimensionality genuinely varies by scientific context.

Use when the unit depends on reaction order, representation choice, pressure-dependent formulation, or imported data conventions.

```
a DOUBLE + a_units ArrheniusAUnits
    -- A-factor units depend on reaction order (unimolecular: s^-1, bimolecular: cm3/mol/s, etc.)

value DOUBLE + value_unit CoordinateUnit
    -- scan coordinate values can be angstrom (bond) or degree (angle/dihedral)

value DOUBLE + value_unit EnergyUnit
    -- applied energy corrections may store results in hartree, kJ/mol, or kcal/mol
```

Applied energy corrections are intentionally stored in their reported scientific units rather than canonicalized on write. This preserves the unit convention used by the correction scheme's original definition, at the cost of requiring unit conversion at read time for cross-row comparison.

All unit enums live in `app/db/models/common.py`. Never define enums inline in model files.

### Current unit enums

| Enum | Members | Used by |
|------|---------|---------|
| `ArrheniusAUnits` | `per_s`, `cm3_mol_s`, `cm3_molecule_s`, `m3_mol_s`, `cm6_mol2_s`, `cm6_molecule2_s`, `m6_mol2_s` | `kinetics.a_units`, `network_kinetics_plog.a_units`, `network_kinetics.rate_units` |
| `CoordinateUnit` | `angstrom`, `degree` | `calc_scan_coordinate.value_unit`, `calc_scan_point_coordinate_value.value_unit` |
| `EnergyUnit` | `hartree`, `kj_mol`, `kcal_mol` | `energy_correction_scheme.units`, `applied_energy_correction.value_unit` |
| `PressureUnit` | `bar`, `atm` | `network_kinetics.pressure_units` |
| `TemperatureUnit` | `kelvin` | `network_kinetics.temperature_units` |

### Why Arrhenius A is contextual

k = A * T^n * exp(-Ea/RT)

The units of A depend on:
- **Reaction order**: unimolecular (s^-1), bimolecular (cm3/mol/s), termolecular (cm6/mol2/s)
- **Representation**: mol-based vs molecule-based
- **Pressure-dependent formulation**: high-P limit vs low-P limit vs PLOG entries

There is no single canonical unit. The enum is required.

### Why thermo, transport, and result energies are canonical

- Thermo: kJ/mol and J/(mol*K) are the SI-adjacent standard used uniformly by NIST, JANAF, and every major thermo database. TCKDB canonicalizes these to fixed units because cross-row comparison is more important than preserving source-unit variety (e.g., kcal/mol, cal/mol/K) that appears in some literature.
- Transport: Lennard-Jones parameters are universally reported in angstrom and kelvin. Debye for dipole moment. No variation.
- Electronic energies: ESS programs output in hartree. Always.

These use Tier 1.

## Tier 3: Free-text (exceptional)

`unit text` is only permitted on parser-observation tables where heterogeneous ESS output is stored verbatim and the unit could be anything (MB, cycles, Hartree, etc.).

Currently the only table using this is `calculation_parameter.unit`.

Free-text is **never** acceptable for:
- Energies, temperatures, pressures
- Geometric quantities (distances, angles)
- Thermodynamic properties
- Rate constants or kinetic parameters
- Transport properties

## Uncertainty columns

### Naming convention for uncertainties

Use `_uncertainty_` in Tier 1 fixed-unit columns.

Examples:
- `ea_kj_mol` + `ea_uncertainty_kj_mol`
- `h298_kj_mol` + `h298_uncertainty_kj_mol`

Use short `d_*` names only where the quantity itself is already conventional and symbol-like, such as:
- `a` + `a_units` + `d_a`
- `n` + `d_n`

Do not introduce mixed naming for the same quantity family (e.g., never combine `d_ea_kj_mol`, `ea_uncertainty_kj_mol`, and `uncertainty_ea_kj_mol`).

### Unit strategy

Uncertainty follows the same unit strategy as the parent quantity.

1. **Tier 1 parent → Tier 1 uncertainty.** Encode the unit in the uncertainty column name.

```
h298_kj_mol              + h298_uncertainty_kj_mol
s298_j_mol_k             + s298_uncertainty_j_mol_k
ea_kj_mol                + ea_uncertainty_kj_mol
electronic_energy_hartree + electronic_energy_uncertainty_hartree
```

2. **Tier 2 parent → inherited unit context.** The uncertainty shares the parent's enum-backed unit field. No separate unit column for the uncertainty.

```
a        + a_units + d_a
    -- d_a is in the same units as a; a_units governs both
```

3. **Free-text uncertainty units are never allowed** outside Tier 3 parser-observation tables.

```
-- BAD
uncertainty DOUBLE
uncertainty_unit TEXT

-- GOOD (Tier 1)
ea_kj_mol DOUBLE
ea_uncertainty_kj_mol DOUBLE

-- GOOD (Tier 2)
a DOUBLE
a_units ArrheniusAUnits
d_a DOUBLE
```

## Input vs storage

API payloads may accept non-canonical units where a schema explicitly allows them.

However, persisted storage must always follow Tier 1 or Tier 2:
- Tier 1 quantities are normalized into the canonical fixed unit before persistence
- Tier 2 quantities persist with an enum-backed unit field
- Tier 3 is reserved only for raw parser-observation tables

## Adding a new quantity

When adding a new column that stores a physical quantity:

1. **Can you name a single canonical unit?** Use Tier 1. Encode the unit in the column name.
2. **Does the unit genuinely vary by scientific context?** Use Tier 2. Create or reuse an enum in `common.py`.
3. **Is this raw parser output with unpredictable units?** Use Tier 3. Document why in the model docstring.

If you reach for `sa.Text()` on a unit field, stop and justify why Tier 1 or 2 doesn't apply.

## PR review checklist

When reviewing schema changes that involve physical quantities:

- [ ] Are units canonical in column names where possible?
- [ ] If a separate unit field exists, is dimensional ambiguity scientifically real?
- [ ] Is the unit field enum-backed rather than free text?
- [ ] Are new enums added to `app/db/models/common.py` (not inline)?
- [ ] Do uncertainty columns mirror the unit strategy of their parent quantity?
- [ ] Do Tier 1 uncertainty columns use `_uncertainty_` rather than `d_*`, with `d_*` reserved for symbol-like kinetic parameters (`d_a`, `d_n`)?
- [ ] If a schema accepts non-canonical units on input, is the conversion to canonical storage explicit at the workflow/service boundary?
- [ ] Is `schema_spec.md` updated to reflect the unit handling?
- [ ] Is the initial migration (`d861dfd60891`) updated for any new enum types?
