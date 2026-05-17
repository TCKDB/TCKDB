# Source-calculation ergonomics

Status: **design / spec only at first revision; Phase 1 now shipped
in `tckdb-client` 0.25.0.** No further implementation work in this
document. Audience: builder maintainers and producers writing
repeated `source_calculations=…` lists.

This doc picks one opt-in helper to land, names what we deliberately
won't add, and lists the order in which the change should happen.
Companion to [`builder_api_mvp.md`](builder_api_mvp.md);
[`conformer_semantic_boundary.md`](conformer_semantic_boundary.md)
governs an unrelated but adjacent boundary question.

---

## 0. Core principles (reproduced here for prominence)

1. Explicit `source_calculations` must remain supported everywhere.
2. Helpers must be opt-in.
3. Helpers must not silently infer provenance from arbitrary calcs.
4. Helpers must not encode ARC-, RMG-, Arkane-, or
   Gaussian-specific assumptions.
5. Helpers return the same shapes builders already accept —
   `dict[str, Calculation]`, `dict[str, list[Calculation]]`, or
   `list[tuple[str, Calculation]]`.
6. The server remains the authority for final validation.
7. Helpers improve readability, not scientific meaning.

Every design decision below derives from these.

---

## 1. Should there be a `SourceCalculations` helper?

**Yes, ship one.** A small, generic, opt-in helper.

The current pain point in demos and producer adapters is the
repeated `list[tuple[str, Calculation]]` literal across blocks that
share most of their calcs:

```python
thermo   = Thermo.nasa(..., source_calculations=[
    ("opt", opt), ("freq", freq), ("sp", sp),
])
statmech = Statmech(..., source_calculations=[
    ("opt", opt), ("freq", freq),
])
```

The producer named each `Calculation` already; restating those names
as string keys twice (once per block) is the friction worth fixing.

### What the helper does **not** do

- It does **not** decide which roles thermo / statmech / transport
  "should" carry. That's domain knowledge, and getting it wrong
  silently is the worst possible behaviour. There's no `.for_thermo()`
  / `.for_statmech()` / `.for_transport()` method, by design — see §3.
- It does **not** validate role tokens against any backend enum.
  Endpoint role vocabularies live in the kinetics / thermo /
  statmech / transport builders, which emit the wire shape and
  raise on unknown roles at that point. The helper is endpoint-agnostic.
- It does **not** check that referenced calcs belong to any upload
  bucket. Bucket residency stays where it is — `ComputedSpeciesUpload`
  / `ComputedReactionUpload` validate it when the helper's output
  gets passed in.

The helper is, on purpose, a tiny adapter that lets producers
*name calcs by role once* and reuse the bundle multiple times.

---

## 2. Alternative: role-list / free-function helpers

Considered and **rejected** in favour of the kwargs-plus-`.only(...)`
shape:

| Option                                                       | Verdict                                                                                                                          |
|--------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| `source_list(("opt", opt), ("freq", freq), ("sp", sp))`      | Barely shorter than the tuple list it replaces. Saves zero conceptual weight. **Skip.**                                          |
| `sources.add("opt", opt); sources.add("freq", freq); …`      | Mutable, line-noisy, encourages thinking of provenance as "things accumulated during a run". **Skip.** Less honest than the literal. |
| `SourceCalculations.from_roles(opt=opt, freq=freq, sp=sp)`   | Only adds value when paired with `.only(...)` for re-emission. **Subsumed** by §3's recommendation — making `from_roles` the implicit kwargs-construction path of the recommended helper. |

The recommended shape combines the kwargs form with explicit subset
selection (§3) and keeps the existing builder shapes valid as-is.

---

## 3. Recommended API

A single small value type and three methods:

```python
@dataclass(eq=False)
class SourceCalculations:
    """Reusable bag of role-tagged Calculation references.

    Construct via kwargs (one role → one Calculation or list of them)
    or via the .add(...) escape hatch for role tokens that aren't
    valid Python identifiers. Pass the result through .only(...) /
    .as_list() to feed any builder that accepts source_calculations.
    """

    # --- Construction ----------------------------------------------------

    def __init__(self, **roles_to_calcs) -> None: ...
    #  SourceCalculations(opt=opt, freq=freq, sp=sp)
    #  SourceCalculations(reactant_energy=[r1_sp, r2_sp], ts_energy=ts_sp, …)

    def add(self, role: str, calc: Calculation) -> "SourceCalculations":
        """Append one (role, calc) entry; returns self for chaining."""

    # --- Emission --------------------------------------------------------

    def as_list(self) -> list[tuple[str, Calculation]]:
        """All entries in insertion order — the form builders accept."""

    def only(self, *roles: str) -> list[tuple[str, Calculation]]:
        """Entries whose role is in `roles`, in **caller-requested order**.

        Roles emit in the order they're passed to ``.only(...)``; entries
        sharing the same role keep their relative source insertion order.
        Raises TCKDBBuilderValidationError when a requested role was
        never registered (typo guard).
        """
```

### Use sites

```python
sources = SourceCalculations(opt=opt, freq=freq, sp=sp)

thermo   = Thermo.nasa(...,    source_calculations=sources.only("opt", "freq", "sp"))
statmech = Statmech(...,       source_calculations=sources.only("opt", "freq"))
```

Kinetics — duplicate roles natural via mixed scalar/list kwargs (same
shape `Kinetics.modified_arrhenius` already accepts as `dict-of-list`):

```python
kin_sources = SourceCalculations(
    reactant_energy=[ch3_sp, h_sp],
    product_energy=[ch4_sp],
    ts_energy=ts_sp,
    freq=ts_freq,
)
kinetics = Kinetics.modified_arrhenius(..., source_calculations=kin_sources.as_list())
```

### Why kwargs-plus-`.only(...)` and not domain-specific methods

A `.for_thermo()` / `.for_statmech()` API would mean baking a
"universal thermo always sources opt+freq+sp, statmech always sources
opt+freq" rule into the client. That's exactly the kind of
workflow-tool-flavoured assumption the principles in §0 forbid.
Real producers ship thermo derived from different role sets
(scalar-only `h298`, NASA + uncertainties, points-only, composite
calcs, …); pretending there's one right answer is an invitation
to slowly accumulate ARC-shaped defaults into the client.

`.only("opt", "freq")` makes the producer's choice **visible at the
call site** — anyone reviewing the diff sees which roles each block
references, without chasing through helper internals.

---

## 4. Kinetics-specific helper

**Skip.** The §3 kwargs form already handles the only thing a
`KineticsSources` would add: list-valued duplicate-role roles. Both
of these construct identical canonical content:

```python
SourceCalculations(reactant_energy=[ch3_sp, h_sp], ts_energy=ts_sp)
KineticsSources(reactant_energy=[ch3_sp, h_sp], ts_energy=ts_sp)
```

The only reason to ship a dedicated `KineticsSources` would be if
its `__init__` named the kinetics roles as typed fields — and
locking that vocabulary into the client would freeze a backend enum
(`KineticsCalculationRole`) at the client layer. When the backend
adds a new kinetics role (e.g. `master_equation`), every client
using `KineticsSources` has to release a new version to support it.
That's an avoidable coupling.

Reconsider if the demos still show repeated duplicate-role
boilerplate after `SourceCalculations` ships and producers report
that the duplicate-role kwargs shape doesn't read well in their
code. Don't pre-build it.

---

## 5. Naming

Considered candidates with verdicts:

| Name                    | Verdict                                                                                                |
|-------------------------|--------------------------------------------------------------------------------------------------------|
| `SourceCalculations`    | **Recommended.** Mirrors the builder kwarg (`source_calculations=`) so the visual symmetry helps. Slightly long, but unambiguous. |
| `SourceCalcSet`         | "Set" implies dedup / unordered; this helper preserves order and duplicates. Rejected for that connotation alone. |
| `SourceRefs`            | Lossy — "refs" reads like FK ids, which is the opposite of what's stored. Rejected.                    |
| `CalculationSources`    | Word-order reversal; would clash visually with `Calculation.source_calculations` attribute. Rejected.   |
| `KineticsSources`       | Domain-specific; subsumed by `SourceCalculations` per §4. Not shipped.                                 |
| `source_calculations(…)`| Function lowercased to match kwarg. Conflicts with the kwarg name where both might appear in scope. Rejected. |
| `source_list(…)`        | Free function; rejected per §2 (no value over a `list[tuple]`).                                       |

Final recommendation: `SourceCalculations`.

---

## 6. Canonical internal representation

Internal storage: `list[tuple[str, Calculation]]`.

Rationale:

- Preserves caller-supplied **insertion order**, which the builders
  already promise to keep deterministic on the wire.
- Preserves **duplicate roles**, which `dict`-keyed storage
  cannot.
- Is exactly the shape `.as_list()` returns and `.only(...)`
  filters from — zero conversion cost at emission time.

The kwargs `__init__` flattens `{role: Calc}` and `{role: [Calc, …]}`
into the canonical list in the order the kwargs were supplied. (On
CPython 3.7+ kwargs preserve insertion order, so this is well-defined.)

`add(role, calc)` appends a single pair. No dict-style overwrite.

---

## 7. Validation responsibilities

| Responsibility                                                | Where it lives                                                                                          |
|---------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| Value is a `Calculation` builder                              | `SourceCalculations.__init__` and `.add(...)`.                                                          |
| `.only(*roles)` rejects unknown requested roles               | `SourceCalculations.only(...)`. Typo guard with a clear error.                                          |
| Role token is in the *endpoint's* role vocabulary             | Each builder (`Thermo`, `Statmech`, `Kinetics`, `Transport`) when it emits — unchanged from today.      |
| Calc references are in the upload's bucket                    | `ComputedSpeciesUpload` / `ComputedReactionUpload` at construction time — unchanged from today.         |
| Wire-shape uniqueness (no duplicate `(calc_key, role)` pairs) | Server (Pydantic validators). Builder doesn't pre-empt this; producers see a clean 422 if they hit it. |

The helper has the lightest possible validation footprint —
catches typed-wrong-thing-as-calc, otherwise stays out of the way.

---

## 8. Interaction with existing builders

`SourceCalculations` does not require any builder change. Every
builder that accepts `source_calculations=` already accepts the three
shapes the helper returns:

- `.as_list()` → `list[tuple[str, Calculation]]` ✓
- `.only(...)` → `list[tuple[str, Calculation]]` ✓
- The kwargs form is *internal* — the helper hands `.as_list()` /
  `.only(...)` to builders.

No producer is forced to migrate. The current shapes —
`dict[str, Calculation]`, `dict[str, list[Calculation]]`,
`list[tuple[str, Calculation]]` — remain valid forever.

**Optional convenience**: builders **could** later add a
`isinstance(value, SourceCalculations)` branch in their
`source_calculations=` parsers so producers can pass the helper
directly. Marginal value; punt on it until producers ask, since
`sources.as_list()` is one method call.

---

## 9. Before / after examples

### Thermo (single species, shared sources)

Before:
```python
thermo = Thermo.nasa(
    coeffs_low=[...], coeffs_high=[...],
    t_low=200, t_mid=1000, t_high=5000,
    h298_kj_mol=-234.0, s298_j_mol_k=281.6,
    source_calculations=[
        ("opt", opt), ("freq", freq), ("sp", sp),
    ],
)

statmech = Statmech(
    external_symmetry=1, point_group="C1",
    is_linear=False, rigid_rotor_kind="asymmetric_top",
    statmech_treatment="rrho",
    source_calculations=[
        ("opt", opt), ("freq", freq),
    ],
)
```

After:
```python
sources = SourceCalculations(opt=opt, freq=freq, sp=sp)

thermo = Thermo.nasa(
    coeffs_low=[...], coeffs_high=[...],
    t_low=200, t_mid=1000, t_high=5000,
    h298_kj_mol=-234.0, s298_j_mol_k=281.6,
    source_calculations=sources.only("opt", "freq", "sp"),
)

statmech = Statmech(
    external_symmetry=1, point_group="C1",
    is_linear=False, rigid_rotor_kind="asymmetric_top",
    statmech_treatment="rrho",
    source_calculations=sources.only("opt", "freq"),
)
```

The role identifiers each block carries are still **visible at the
call site** — there is no hidden default.

### Kinetics (bimolecular reaction, duplicate roles)

Before:
```python
kinetics = Kinetics.modified_arrhenius(
    A=1.2e13, A_units="cm3/mol/s", n=0.5, Ea=10.0, Ea_units="kJ/mol",
    Tmin=300, Tmax=2500,
    source_calculations=[
        ("reactant_energy", ch3_sp),
        ("reactant_energy", h_sp),
        ("product_energy",  ch4_sp),
        ("ts_energy",       ts_sp),
        ("freq",            ts_freq),
    ],
)
```

After:
```python
kin_sources = SourceCalculations(
    reactant_energy=[ch3_sp, h_sp],
    product_energy=[ch4_sp],
    ts_energy=ts_sp,
    freq=ts_freq,
)
kinetics = Kinetics.modified_arrhenius(
    A=1.2e13, A_units="cm3/mol/s", n=0.5, Ea=10.0, Ea_units="kJ/mol",
    Tmin=300, Tmax=2500,
    source_calculations=kin_sources.as_list(),
)
```

Bimolecular-side lists read naturally; no `("reactant_energy", …)`
tuple is repeated.

### Escape hatch: role tokens that aren't Python identifiers

For role tokens that aren't valid identifiers (none in today's
backend enums, but trivial to anticipate — e.g. `"k-inf"`):

```python
sources = SourceCalculations(
    opt=opt, freq=freq, sp=sp,
).add("k-inf", sp_kinf).add("k-zero", sp_kzero)
```

---

## 10. What we deliberately do **not** do

Each item below is a footgun the principles in §0 rule out.

- **No automatic inference.** No "if you don't pass
  `source_calculations`, the builder will fill them from your
  `ComputedSpeciesUpload.calculations`." Provenance is the producer's
  responsibility; silent inference would invert that.
- **No domain-specific defaults.** No `.for_thermo()`, no
  `.for_statmech()`, no `.for_transport()`. Those methods would
  encode the helper's opinion of what each block "should" reference
  — and getting that opinion wrong is undetectable from the call
  site.
- **No `ARCDefaults` / `RMGDefaults` / `ArkaneDefaults` presets.**
  The client stays workflow-tool-agnostic. Producer-side adapters
  remain the right place for tool-specific habits (e.g. an ARC
  adapter that constructs `SourceCalculations` from an ARC
  conformer-search summary file).
- **No "smart" merging.** If a producer constructs two
  `SourceCalculations` and wants their union, they call
  `SourceCalculations.from_pairs(a.as_list() + b.as_list())` or just
  use `a.add(...)` chains. The helper does not ship a `merge` or `|`
  operator.
- **No quietly making `source_calculations=` optional.** Every
  builder that accepts the kwarg keeps treating absence as "no
  declared provenance"; the helper does not change that. If a
  producer's data legitimately has no source calcs, they pass
  `None` (or omit the kwarg), as today.
- **No role-vocabulary validation in the helper.** Endpoint role
  enums (`ThermoCalculationRole`, `StatmechCalculationRole`,
  `KineticsCalculationRole`, `TransportCalculationRole`) stay where
  they are. A future producer that adds a misspelled role
  (`"reactant_eenergy"`) sees the existing builder-level error —
  same code path, same message.

---

## 11. Implementation recommendation

A two-phase rollout, no more:

### Phase 1 — `SourceCalculations`

- Ship the value type from §3 in a minor release (likely 0.25.0).
- New module: `tckdb_client/builders/sources.py`. Re-export
  `SourceCalculations` from `tckdb_client.builders` so producers
  see one canonical home.
- One pure-Python file, no new dependencies, no backend changes.
- Tests cover §6 (order / duplicates), §7 (typo guard, calc-type
  check), and §8 (existing shapes unchanged).
- Demos and the notebook adopt the helper in §9's shape. The
  reaction demo's repeated tuple lists collapse into one
  `SourceCalculations` instance reused across thermo / statmech /
  kinetics.

### Phase 2 — Reassess `KineticsSources`

- **Only if** post-Phase-1 demos still show repeated
  duplicate-role tuple lists. The most likely outcome is that the
  `reactant_energy=[…]` kwargs form removes the friction without
  a dedicated helper.
- If a `KineticsSources` ever lands, it must wrap (not replace)
  `SourceCalculations` so there's no double-storage of the same
  data.

### Skip

- Source-summary / pretty-print helpers (`sources.summary()`,
  `__rich_repr__`, …). Producers who want this can `repr()` the
  helper's `.as_list()` output. Revisit only with a concrete
  request from a real producer.

---

## 12. Test plan for future implementation

These tests should land alongside the Phase-1 implementation, not
in this design PR:

| Test                                                                                  | Why                                                                                                |
|---------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| `SourceCalculations(...)` preserves insertion order across kwargs                     | The wire-shape determinism the builders rely on.                                                   |
| `SourceCalculations(...)` accepts single `Calculation` and `list[Calculation]` values | The kwargs shape ergonomics (§3).                                                                  |
| `.only("a", "b")` returns `list[tuple[str, Calculation]]` in caller-requested role order | The emission contract — explicit at the call site, not implicit from source order.              |
| `.only("nope")` raises `TCKDBBuilderValidationError` with the misspelled role         | The typo guard (§7).                                                                               |
| `.as_list()` preserves duplicate roles                                                | Bimolecular kinetics use case (§9).                                                                |
| `SourceCalculations` rejects non-`Calculation` values at construction                 | Lightest-possible validation (§7).                                                                 |
| `.add(role, calc)` returns self for chaining                                          | Builder-chain style.                                                                               |
| Existing builders accept all three of: dict, dict-of-list, list-of-tuples             | No regression in existing producer code.                                                           |
| No builder secretly fills `source_calculations` from elsewhere                        | Inference is forbidden (§10). A scan-the-builder test catches a future maintainer reintroducing it. |
| `Thermo`, `Statmech`, `Kinetics` accept `sources.as_list()` and `sources.only(...)`   | Drop-in compatibility (§8).                                                                        |

---

## 13. Non-goals

Reiterated for emphasis:

- **No implementation in this task.** Design only.
- **No backend schema changes.** The helper's output is one of the
  three shapes builders already accept; the wire shape is unchanged.
- **No source-calculation inference.** Implicit / "smart" defaults
  are out of scope here and out of scope forever, per §10.
- **No parser implementation.** No "parse from ARC summary file",
  no "extract from output.yml", no "import from a JSON manifest".
  Producer adapters live where adapters live.
- **No ARC-specific (or other workflow-tool-specific) presets.**
- **No changes to existing `source_calculations` accepted shapes.**
  `dict[str, Calculation]`, `dict[str, list[Calculation]]`, and
  `list[tuple[str, Calculation]]` keep working.

---

## See also

- [`builder_api_mvp.md`](builder_api_mvp.md) — the broader builder spec.
- [`builder_api_stability.md`](builder_api_stability.md) — public-beta
  contract; a future `SourceCalculations` ships as a public-beta
  addition under that policy.
- [`conformer_semantic_boundary.md`](conformer_semantic_boundary.md)
  — the matching policy doc for the conformer boundary.
- [`adapter_authoring_quickstart.md`](adapter_authoring_quickstart.md)
  — the producer-facing quickstart; §6 walks the recommended
  `SourceCalculations` use shape for adapters.
