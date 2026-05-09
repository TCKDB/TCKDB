# Spec: General path-search calculation schema for NEB/GSM TS-guess provenance

## 1. Purpose

TCKDB needs a general way to represent path-search calculations used to generate transition-state guesses.

Today, the schema has a NEB-specific calculation/result concept:

```text
calculation.type = neb
calc_neb_image_result
```

That is too narrow because ARC can produce TS guesses using other established path-search methods, especially:

```text
NEB
GSM / xTB-GSM
```

A recent ARC smoke test showed exactly this situation: NEB ran successfully, but ARC selected `xtb-gsm` as the TS guess used for the final TS optimization. The current TCKDB schema has a first-class `neb` calculation type but no equivalent place for GSM.

The long-term schema should represent the general concept:

```text
path-search calculation
```

with the method stored as data:

```text
method = neb | gsm | ...
```

rather than making every algorithm its own top-level calculation type.

## 2. Core modeling decision

Replace the NEB-specific top-level calculation type with a general path-search calculation type.

Preferred model:

```text
calculation.type = path_search
calc_path_search_result.method = neb | gsm | ...
```

Instead of:

```text
calculation.type = neb
```

This allows:

```text
ts_guess(path_search, method=neb) -> ts_opt
ts_guess(path_search, method=gsm) -> ts_opt
```

using the existing dependency role:

```text
dependency_role = optimized_from
```

## 3. Scientific interpretation

A path-search calculation is a calculation that explores a reaction path between or from molecular endpoints and can produce a TS guess.

Examples:

```text
NEB
GSM / xTB-GSM
growing string
freezing string
other future path methods
```

Path-search calculations are different from:

```text
heuristic TS guesses
template-generated TS guesses
user-supplied XYZ guesses
```

Those geometry-only guesses should not be represented as calculation parents unless ARC actually ran a calculation and can emit real calculation provenance.

## 4. Calculation type

Add or replace with:

```text
CalculationType.path_search
```

Preferred pre-release choice:

```text
remove/replace CalculationType.neb with CalculationType.path_search
```

NEB should become a method of a path-search calculation:

```text
calculation.type = path_search
path_search_result.method = neb
```

Fallback only if replacement is too disruptive:

```text
keep CalculationType.neb temporarily
add CalculationType.path_search
document NEB-specific path as legacy/pre-generalization
```

Because the project is pre-release and still folds changes into the initial migration, prefer the cleaner replacement.

## 5. New enum

Add:

```text
PathSearchMethod
```

Suggested values:

```text
neb
gsm
growing_string
freezing_string
other
```

If naming conventions prefer suffixes:

```text
path_search_method
```

Use the project’s existing enum style.

## 6. New tables

### 6.1 `calc_path_search_result`

One row per path-search calculation.

Suggested fields:

```text
calculation_id                  PK/FK calculation.id
method                          PathSearchMethod, required
is_double_ended                 boolean nullable
converged                       boolean nullable
n_points                        integer nullable
selected_ts_point_index         integer nullable
zero_energy_reference_hartree   float nullable
note                            text nullable
```

Optional fields if useful and low-risk:

```text
climbing_image_index            integer nullable
source_endpoint_count           integer nullable
```

Constraints:

```text
n_points IS NULL OR n_points >= 1
selected_ts_point_index IS NULL OR selected_ts_point_index >= 0
climbing_image_index IS NULL OR climbing_image_index >= 0
source_endpoint_count IS NULL OR source_endpoint_count >= 1
```

### 6.2 `calc_path_search_point`

One row per path/image/node point.

Suggested fields:

```text
calculation_id                  FK calculation.id
point_index                     integer
electronic_energy_hartree       float nullable
relative_energy_kj_mol          float nullable
geometry_id                     FK geometry.id nullable
path_coordinate                 float nullable
max_force                       float nullable
rms_force                       float nullable
max_gradient                    float nullable
rms_gradient                    float nullable
is_ts_guess                     boolean default false
is_climbing_image               boolean default false
note                            text nullable
```

Primary key:

```text
(calculation_id, point_index)
```

Constraints:

```text
point_index >= 0
```

This table generalizes:

```text
NEB images
GSM nodes
string-method path points
```

## 7. Payload shape

### 7.1 GSM example

```yaml
key: ts_guess
type: path_search
path_search_result:
  method: gsm
  is_double_ended: true
  converged: true
  n_points: 15
  selected_ts_point_index: 8
  points:
    - point_index: 0
      relative_energy_kj_mol: 0.0
      geometry: ...
    - point_index: 8
      relative_energy_kj_mol: 44.2
      is_ts_guess: true
      geometry: ...
```

### 7.2 NEB example

```yaml
key: ts_guess
type: path_search
path_search_result:
  method: neb
  is_double_ended: true
  converged: true
  n_points: 7
  selected_ts_point_index: 4
  points:
    - point_index: 0
      relative_energy_kj_mol: 0.0
      geometry: ...
    - point_index: 4
      relative_energy_kj_mol: 52.1
      is_ts_guess: true
      is_climbing_image: true
      geometry: ...
```

### 7.3 TS optimization dependency

```yaml
transition_state:
  calculations:
    - key: ts_guess
      type: path_search
      path_search_result:
        method: gsm

  calculation:
    key: ts_opt
    type: opt
    depends_on:
      - parent_calculation_key: ts_guess
        role: optimized_from
```

## 8. Validation rules

A calculation with:

```text
type = path_search
```

may include:

```text
path_search_result
```

A calculation with:

```text
type != path_search
```

must reject:

```text
path_search_result
```

A calculation with:

```text
type = path_search
```

must reject unrelated result blocks such as:

```text
opt_result
freq_result
sp_result
scan_result
irc_result
```

unless there is already a documented exception.

If replacing NEB:

```text
neb_result
```

should be removed or renamed to:

```text
path_search_result
```

Any legacy NEB validators should be updated.

## 9. Dependency semantics

Use existing dependency role:

```text
optimized_from
```

for:

```text
path_search TS guess -> TS optimization
```

Do not add a new dependency role.

Valid:

```text
ts_guess(path_search, method=neb) -> ts_opt(opt)
ts_guess(path_search, method=gsm) -> ts_opt(opt)
```

Invalid / not intended:

```text
heuristic geometry -> ts_opt as calculation_dependency
user XYZ -> ts_opt as calculation_dependency
template guess -> ts_opt as calculation_dependency
```

Geometry-only provenance should remain:

```text
calculation_input_geometry
```

not:

```text
calculation_dependency
```

## 10. Existing NEB table migration/refactor

Audit all current references to:

```text
calc_neb_image_result
NEBResultPayload
neb_result
CalculationType.neb
```

Preferred result:

```text
replace with generic path_search equivalents
```

Expected replacements:

```text
calc_neb_image_result        -> calc_path_search_point
NEBResultPayload             -> PathSearchResultPayload
neb_result                   -> path_search_result
CalculationType.neb          -> CalculationType.path_search
```

NEB-specific fields should map as:

```text
image_index                  -> point_index
is_climbing_image            -> is_climbing_image
path_distance_angstrom       -> path_coordinate, if semantically acceptable
max_force                    -> max_force
rms_force                    -> rms_force
relative_energy_kj_mol       -> relative_energy_kj_mol
electronic_energy_hartree    -> electronic_energy_hartree
```

If `path_distance_angstrom` is not a universal path coordinate, either:

```text
keep as path_coordinate
```

or add:

```text
path_coordinate_unit
```

Only add the unit field if current project unit policy supports it cleanly.

## 11. Read API behavior

Add or update calculation read APIs so path-search results are readable.

Preferred generic endpoint/shape:

```text
GET /api/v1/calculations/{id}/path-search-result
```

or include in existing calculation result aggregation if that is the current pattern.

If there is a NEB-specific route, either:

```text
replace with path-search route
```

or:

```text
keep route as compatibility alias only if needed
```

Because the project is pre-release, prefer generic replacement.

## 12. Record review behavior

Path-search calculations are calculations and should receive `record_review` rows under the existing calculation review behavior.

No special record-review table changes are required beyond ensuring:

```text
record_type = calculation
record_id = path_search calculation id
status = not_reviewed / under_review / approved / rejected / deprecated
```

according to the existing `ReviewPolicy`.

## 13. Non-goals

Do not change ARC in this schema task.

Do not implement GSM parsing in ARC yet.

Do not implement NEB/GSM scientific parsers unless already trivial.

Do not model heuristic/user-supplied TS guesses as path-search calculations.

Do not add new dependency roles.

Do not add frontend work.

Do not add a historical path-search event table.

## 14. Tests required

Add/update backend tests for:

```text
path_search calculation accepts path_search_result
path_search_result rejects non-path_search calculations
method enum accepts neb
method enum accepts gsm
path_search points persist
path_search points can include geometry
path_search ts_guess can be parent of ts_opt through optimized_from
computed-reaction bundle with ts_guess path_search validates
old neb_result payload is rejected or migrated according to chosen policy
invalid result/type combinations fail early
read API returns path-search result
```

If replacing NEB-specific tables, update all existing NEB tests to use:

```text
path_search_result.method = neb
```

## 15. Documentation required

Update `schema_spec.md` to explain:

```text
Path-search calculations represent path-based TS-search methods such as NEB and GSM.

NEB and GSM are methods of a path-search calculation, not separate top-level calculation provenance concepts.

A path-search calculation may serve as the parent of a TS optimization through calculation_dependency.role = optimized_from.

Heuristic or user-supplied TS guesses remain geometry-only unless a real calculation was run and represented.
```

Update DBML/schema docs if generated/maintained.

## 16. Acceptance criteria

The task is complete when:

```text
CalculationType.path_search exists.
PathSearchMethod enum exists.
Generic path-search result/point persistence works.
NEB-specific schema/code is removed or explicitly documented as retained fallback.
Computed-reaction bundles can validate a path_search ts_guess parent.
All old NEB references have been audited.
Tests pass.
schema_spec.md documents the new model.
```
