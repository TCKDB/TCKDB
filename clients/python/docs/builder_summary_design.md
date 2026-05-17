# Builder summary / describe API — design

Status: **design / spec at first revision; Phase 1 now shipped
in `tckdb-client` 0.26.0.** No further implementation work in this
document. Audience: builder maintainers and producers writing
CLI / notebook preview tooling.

Companion to [`builder_api_mvp.md`](builder_api_mvp.md),
[`builder_api_stability.md`](builder_api_stability.md),
[`source_calculation_ergonomics.md`](source_calculation_ergonomics.md),
and [`parser_validation_boundary.md`](parser_validation_boundary.md).
This doc picks one small public surface for *human-readable / log-shaped
previews* of upload builders, distinguishes it sharply from the
authoritative `to_payload()` wire representation, and lists the
order in which the change should happen.

---

## 0. Why this surface exists

Both demos (`builder_computed_species_demo.py`,
`builder_computed_reaction_demo.py`) carry their own
`_payload_summary(payload: dict) -> str` helper. That helper drifted
slightly between the two demos, and any third producer reaching for
the same shape would write a fourth copy. CLI dry-runs, notebook
previews, workflow logs, pre-upload reviews, and ad-hoc debugging
all want the *same* terse, structured snapshot of an upload — and
none of them want the full payload JSON.

This document picks a builder-side method for that snapshot. The
strict rules:

1. The summary is **not a second schema** and **not a substitute**
   for `to_payload()`. `to_payload()` remains the canonical wire
   representation; the summary is a human-shaped *viewer* of the
   same underlying builder state.
2. The summary must be **derivable from the builder objects alone**,
   without going through `to_payload()`. (`to_payload()` is the
   wire compiler; `summary()` is a builder-side viewer.) The two
   must agree on what's there — if they ever disagree, one of
   them is buggy and `to_payload()` wins.
3. The summary is for **humans and tests**. It is not a transport
   format.

---

## 1. API name

A single small method on `ComputedSpeciesUpload` and
`ComputedReactionUpload`. Candidates considered:

| Candidate           | Verdict                                                                                                       |
|---------------------|---------------------------------------------------------------------------------------------------------------|
| `upload.summary()`  | **Recommended.** Short, familiar (`pandas.DataFrame.describe()`, `tensorflow.Model.summary()`), and matches CLI / log mental model — "give me the summary". |
| `upload.describe()` | Acceptable. Slight `pandas` connotation that the result is a per-row report rather than a per-upload digest.  |
| `upload.preview()`  | Implies "before-and-after"; encourages mistaken assumption that there's an "after". Rejected.                  |
| `upload.to_summary()` | Mirrors `to_payload()` / `to_text()` / `to_dict()` patterns. Acceptable, but verbose for a method that producers will reach for often. |
| `upload.info()`     | Used by NumPy / pandas for something different (memory / dtype counts). Avoid the name collision.              |

Recommendation: **`summary()`**. The two-syllable verb form keeps
the call site terse and parallels `to_payload()` without colliding
with it.

---

## 2. Return type

The summary needs to feed two audiences:

- A *human* reading a CLI dump or a notebook cell.
- A *test or log line* asserting on structured fields.

Candidates:

| Candidate                                           | Verdict                                                                                                                                                                                                                  |
|-----------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Plain `str`                                         | Cheap, but pushes formatting onto callers; impossible to assert on fields without re-parsing.                                                                                                                            |
| Plain `dict`                                        | Good for tests; pushes formatting onto callers; type-unsafe (any key, any value).                                                                                                                                        |
| Frozen `@dataclass`                                 | Type-safe; closed shape; no `.to_text()` without bolting one on; evolution requires bumping the dataclass.                                                                                                                |
| Small object with `.to_text()` and `.to_dict()`     | **Recommended.** Bundles the two audiences; lets each evolve independently; gives a single public type (`UploadSummary`) that the stability policy can pin without freezing the human formatting.                          |
| `__rich__` / pandas Series / `nbformat`-shaped repr | Pulls in dependencies the base client deliberately does not ship. Rejected.                                                                                                                                              |

Recommendation: a small object (working name `UploadSummary`) with
two emission methods:

```python
summary = upload.summary()

print(summary.to_text())              # human-readable; formatting may change
data = summary.to_dict()              # stable keys; ergonomic for tests/logs
assert data["artifact_count"] == 2
```

Implementation sketch (illustrative, not prescriptive):

```python
@dataclass(frozen=True)
class UploadSummary:
    kind: str                                    # "computed_species" / "computed_reaction"
    fields: dict[str, Any]
    sections: tuple[SummarySection, ...]

    def to_dict(self) -> dict[str, Any]: ...
    def to_text(self) -> str: ...
```

Keys / fields are detailed in §3 and §4. The object is **frozen**;
producers who want to tweak text should compose, not mutate.

---

## 3. What `summary()` should include

The summary's job is *terse but complete enough to spot mistakes*.
Every field below is intentionally small: counts, identifiers, and
booleans rather than full content.

### `ComputedSpeciesUpload.summary()`

Top-level fields:

| Key                              | Value                                                          |
|----------------------------------|----------------------------------------------------------------|
| `kind`                           | `"computed_species"`                                           |
| `species_smiles`                 | `Species.smiles` (or `None`)                                   |
| `species_label`                  | `Species.label` (or `None`)                                    |
| `charge`                         | `Species.charge`                                               |
| `multiplicity`                   | `Species.multiplicity`                                         |
| `conformer_record_count`         | Number of conformer records currently emitted (today: always 1, per the conformer-boundary policy) |
| `calculation_count`              | Total `Calculation` count on the upload                        |
| `calculation_counts_by_type`     | `dict[str, int]` keyed by `opt`/`freq`/`sp`/`scan`/…           |
| `primary_calculation_label`      | `primary_calculation.label` (or `None`)                        |
| `primary_calculation_key`        | `primary_calculation.local_key()` if minted, else `None`       |
| `primary_calculation_type`       | `primary_calculation.type`                                     |
| `has_thermo`                     | `bool`                                                         |
| `thermo_kind`                    | `"scalar" | "nasa" | "points" | None`                          |
| `has_statmech`                   | `bool`                                                         |
| `has_transport`                  | `bool`                                                         |
| `artifact_count`                 | Total artifacts attached across all calculations               |
| `artifact_calculation_count`     | Distinct calculations carrying ≥1 artifact                     |
| `diagnostic_count`               | `len(upload.emission_diagnostics())`                           |
| `diagnostic_codes`               | Sorted unique `Diagnostic.code` list (just codes, not messages) |

### `ComputedReactionUpload.summary()`

Top-level fields:

| Key                                      | Value                                                                 |
|------------------------------------------|-----------------------------------------------------------------------|
| `kind`                                   | `"computed_reaction"`                                                 |
| `reactant_smiles`                        | `list[str | None]` — one entry per reactant `Species`                 |
| `product_smiles`                         | `list[str | None]` — one entry per product `Species`                  |
| `reactant_labels`                        | `list[str | None]`                                                    |
| `product_labels`                         | `list[str | None]`                                                    |
| `reaction_family`                        | Family name if set, else `None`                                       |
| `species_count`                          | Total distinct species in the upload                                  |
| `ts_calculation_counts_by_type`          | `dict[str, int]` — TS-bucket calcs by `Calculation.type`              |
| `species_calculation_counts`             | `dict[species_key, int]` — count per species                          |
| `species_calculation_counts_by_type`     | `dict[species_key, dict[str, int]]`                                   |
| `kinetics_count`                         | Length of `ChemReaction.kinetics`                                     |
| `species_with_thermo`                    | `list[species_key]`                                                   |
| `species_with_statmech`                  | `list[species_key]`                                                   |
| `species_with_transport`                 | `list[species_key]`                                                   |
| `artifact_count`                         | Total artifacts across the upload                                     |
| `artifact_calculation_count`             | Distinct calculations carrying ≥1 artifact                            |
| `diagnostic_count`                       | `len(upload.emission_diagnostics())`                                  |
| `diagnostic_codes`                       | Sorted unique `Diagnostic.code` list                                  |

The `species_key` values are the same local keys
`ComputedReactionUpload.to_payload()` mints, so producers can
correlate a summary line to a payload entry without an extra
lookup.

### Section layout for `.to_text()`

For both upload kinds, `.to_text()` emits a fixed ordered set of
sections (illustrative — exact wording is not stable):

```text
identity / species
calculations
scientific blocks (thermo / statmech / transport / kinetics)
artifacts
diagnostics (codes only)
```

Producers who want to slice the text — e.g. show only the
"calculations" block — should not parse `.to_text()`; they should
use `.to_dict()` instead. The text method is *narrative*; the dict
is *structured*.

---

## 4. What `summary()` should NOT include

The summary is a digest, not a partial payload. The following are
**explicitly excluded** so the surface stays small and the
authoritative representation stays `to_payload()`:

- The full payload JSON or any substring of it.
- Any raw XYZ block. Geometries are summarised by the *fact* of
  their presence (or by atom count if the field is genuinely
  worth surfacing), never by their literal text.
- Any base64 / binary artifact content. Artifact reporting is
  count-only (§3) — bytes ride on the second-phase POST, not
  through the summary.
- Full NASA coefficients (the 14 floats per thermo). Surface
  `thermo_kind="nasa"` and stop.
- Full frequency lists. The presence of a `freq` calculation is
  in the counts; producers who want the frequencies should hit
  `to_payload()` or the calculation builder directly.
- Database / server-minted IDs. The summary is *pre-upload*;
  server IDs are not yet known. Even *after* a server response,
  the summary stays builder-shaped — IDs live on `result`, not
  on the summary.
- Server-side moderation / review status. The summary never
  reaches the network, so it has no business reporting that.

The rule of thumb: if a field is large, slow to format, or
post-upload-only, it does not belong in the summary.

---

## 5. Relationship to emission diagnostics

The builder already exposes a stable diagnostics surface:

```python
diags = upload.emission_diagnostics()        # list[Diagnostic]
for d in diags:
    print(d.code, d.level, d.path, d.message)
```

The summary's diagnostic story is intentionally limited:

- `summary.diagnostic_count: int` — total diagnostics.
- `summary.diagnostic_codes: tuple[str, ...]` — sorted unique
  `Diagnostic.code` values (stable tokens, listed in `DIAG_CODES`).
- **No diagnostic messages or paths.** Detail lives in
  `emission_diagnostics()`.

Rationale:

1. The codes are the load-bearing identifier — they're already
   under the public-beta stability policy in
   [`builder_api_stability.md`](builder_api_stability.md).
2. Long human messages and JSON-path strings are exactly the kind
   of "long text" §4 forbids in the summary.
3. The codes give producers enough signal to decide whether to
   call `emission_diagnostics()` for the full report.

If a future producer needs richer summary diagnostics, that is a
deliberate API extension, not a default — and the appropriate
escape hatch is `emission_diagnostics()` itself, not enlarging the
summary.

---

## 6. Relationship to artifact planning

Artifact planning has its own surface, already implemented:

```python
upload.artifact_plan(result)            # needs server response
upload.artifact_plan_preview(...)       # offline preview
upload.iter_artifacts()                 # builder-side iteration
```

The summary touches artifacts only at the *count* level:

- `summary.artifact_count: int` — total artifacts attached to all
  calculations across the upload.
- `summary.artifact_calculation_count: int` — number of distinct
  calculations that carry at least one artifact.

The summary does **not** include:

- `PlannedArtifactUpload` records.
- Artifact paths, hashes, kinds, or labels.
- Mock or real `calculation_id` mappings.

Producers wanting plan-shaped detail keep using
`artifact_plan_preview()` (offline) or `artifact_plan(result)`
(after server response). The summary is just the "are there any"
gut check.

---

## 7. Stability

Public-beta surface, layered carefully so the text formatting can
evolve without breaking tests / scripts:

| Surface                          | Stability                                                                                                  |
|----------------------------------|------------------------------------------------------------------------------------------------------------|
| `upload.summary()` method itself | **Public beta** once shipped. Renames or removals are major-version events.                                |
| `UploadSummary` type             | **Public beta** as a type alias only — producers should `from tckdb_client.builders import UploadSummary` rather than reaching into module internals. |
| `summary.to_dict()` **keys**     | **Public beta.** Adding new keys is a minor-version event; removing or renaming a key is a major-version event. |
| `summary.to_dict()` **values**   | Public beta for *type* (int stays int, str stays str). Specific text values may evolve only when the underlying field semantics change. |
| `summary.to_text()` formatting   | **Not stable.** Whitespace, ordering, separators, and section headers may change between minor versions. Tests must not assert on substrings of `.to_text()`. |
| `diagnostic_codes` values        | Tracked separately under `DIAG_CODES` and [`builder_api_stability.md`](builder_api_stability.md). The summary just re-exposes the codes already governed there. |
| New summary fields               | May be added in minor versions if value type is stable. New fields must not become required-to-read keys for existing tests.                                  |

Stability is the *single biggest reason* §2 recommends a wrapper
object over a plain dict: the wrapper can grow `.to_text()` features
behind a non-stable formatter while the `.to_dict()` keys carry
the public contract.

---

## 8. Example

```python
from tckdb_client.builders import (
    Calculation, ComputedSpeciesUpload, Geometry, LevelOfTheory,
    Species, SoftwareRelease, Thermo,
)

sr  = SoftwareRelease(software="Gaussian", version="16")
lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
geom = Geometry.from_xyz("3\nh2o\nO 0 0 0.117\nH 0 0.757 -0.469\nH 0 -0.757 -0.469")

opt  = Calculation.opt(sr, lot, output_geometry=geom, final_energy_hartree=-76.4,
                       converged=True, label="opt")
freq = Calculation.freq(sr, lot, input_geometry=geom, n_imag=0,
                        zpe_hartree=0.021, depends_on=opt, label="freq")
sp   = Calculation.sp(sr, lot, input_geometry=geom, electronic_energy_hartree=-76.45,
                      depends_on=opt, label="sp")
opt.add_artifact("water_opt.log", kind="output_log")
sp.add_artifact("water_sp.log", kind="output_log")

upload = ComputedSpeciesUpload(
    species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
    calculations=[opt, freq, sp],
    primary_calculation=opt,
    thermo=Thermo.scalar(h298_kj_mol=-241.8, s298_j_mol_k=188.8,
                         source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)]),
)

summary = upload.summary()

# Human-readable; formatting may change between minor versions.
print(summary.to_text())
# >>> ComputedSpeciesUpload — 'water' (smiles='O', charge=0, mult=1)
# >>>   calculations:   3 total  (opt=1, freq=1, sp=1)
# >>>   primary:        'opt'  (type=opt)
# >>>   scientific:     thermo=scalar, statmech=no, transport=no
# >>>   artifacts:      2 across 2 calculation(s)
# >>>   diagnostics:    0

# Structured; keys are public-beta stable.
data = summary.to_dict()
assert data["kind"] == "computed_species"
assert data["species_smiles"] == "O"
assert data["calculation_counts_by_type"] == {"opt": 1, "freq": 1, "sp": 1}
assert data["artifact_count"] == 2
assert data["artifact_calculation_count"] == 2
assert data["has_thermo"] is True
assert data["thermo_kind"] == "scalar"
assert data["has_statmech"] is False
assert data["diagnostic_count"] == 0
```

`ComputedReactionUpload.summary()` follows the same pattern:

```python
summary = reaction_upload.summary()
print(summary.to_text())
# >>> ComputedReactionUpload — CH3 + H -> CH4  (family=H_Abstraction)
# >>>   species:        3       (CH3, H, CH4)
# >>>   ts calcs:       3 total (opt=1, freq=1, sp=1)
# >>>   species calcs:  CH3=3, H=3, CH4=3
# >>>   kinetics:       1
# >>>   scientific:     thermo on [CH4], statmech on [CH4], transport on [CH4]
# >>>   artifacts:      2 across 2 calculation(s)
# >>>   diagnostics:    2 codes: thermo_source_calculations_not_emitted_in_computed_reaction_bundle, transport_not_emitted_in_computed_reaction_bundle

data = summary.to_dict()
assert data["kind"] == "computed_reaction"
assert data["reaction_family"] == "H_Abstraction"
assert data["kinetics_count"] == 1
```

Migration: the current demo `_payload_summary(payload)` helpers
collapse to one line.

```python
# Today (demo-local):
print(_payload_summary(upload.to_payload()))

# After this design ships:
print(upload.summary().to_text())
```

---

## 9. Test plan for future implementation

Tests to land **with** the Phase-1 implementation, not in this
design PR:

| Test                                                                                  | Why                                                                                          |
|---------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| `ComputedSpeciesUpload.summary()` returns the §3 species-side field set               | The public-beta key contract.                                                                |
| `ComputedReactionUpload.summary()` returns the §3 reaction-side field set             | Same, for the reaction path.                                                                 |
| `summary.to_text()` includes the §3 section labels (identity / calcs / sci / artifacts / diagnostics) | Smoke test for human readability; substring-only, never exact-match.                          |
| `summary.to_dict()` contains no XYZ text and no base64 content                         | §4 enforcement.                                                                              |
| `summary.diagnostic_codes` is a subset of `DIAG_CODES`                                 | §5 / stability invariant.                                                                    |
| `summary.diagnostic_count` matches `len(upload.emission_diagnostics())`                | Cross-consistency with the existing diagnostics surface.                                     |
| `summary.artifact_count` matches `sum(1 for _ in upload.iter_artifacts())`             | Cross-consistency with the existing artifact iteration surface.                              |
| `summary.artifact_calculation_count` matches `len({entry.calculation for entry in upload.iter_calculation_entries(with_artifacts_only=True)})` | Same, for the per-calculation count.                                                         |
| Existing demos: replacing `_payload_summary(payload)` with `upload.summary().to_text()` keeps the demo smoke tests passing | The migration is the point of this surface.                                                  |
| `summary.to_dict()` round-trip: keys + value types are stable across two consecutive `summary()` calls | Determinism.                                                                                 |
| `summary.to_dict()` contains no keys whose values are dicts holding raw payload-shaped chunks | §4 — the digest does not embed the wire.                                                     |

---

## 10. Non-goals

Reiterated for emphasis:

- **No implementation in this task.** Design only.
- **No backend schema changes.** The summary is a pure builder-side
  view; nothing leaves the client.
- **No server-side `/summary` endpoint.** The wire surface stays
  identical to today.
- **No full payload pretty-printer.** Producers who want the wire
  representation already have `json.dumps(upload.to_payload(),
  indent=2)`; the summary is not a competitor to that.
- **No `rich` / `tabulate` / `pandas` dependency.** Base
  `tckdb-client` stays small; `to_text()` is plain string output.
  Producers can pretty-print on their own using `to_dict()`.
- **No notebook-specific rendering yet** (no `_repr_html_`, no
  `__rich__`). Revisit only when a real producer ships a notebook
  workflow that genuinely needs it; until then, `to_text()` and
  `to_dict()` cover both audiences.
- **No mutation hooks.** `UploadSummary` is frozen; producers
  composing custom views build their own object on top of
  `to_dict()`.
- **No SMILES / geometry canonicalisation in the summary.**
  Identity normalisation belongs to the backend (and ultimately
  to curation), not to a viewer surface.

---

## 11. Suggested implementation phases

A two-phase rollout, no more:

### Phase 1 — `UploadSummary` + `summary()` method

- New module: `tckdb_client/builders/summary.py`. Defines
  `UploadSummary` (frozen dataclass) and the per-upload-kind
  collectors.
- Re-export `UploadSummary` from `tckdb_client.builders` so the
  public type surface is one canonical home.
- New `summary()` method on `ComputedSpeciesUpload` and
  `ComputedReactionUpload`. Pure-Python, no new dependencies.
- Tests per §9; demos migrate from `_payload_summary(...)` to
  `upload.summary().to_text()`.
- Version bump: minor (additive). Diagnostic-code surface
  unchanged.

### Phase 2 — Reassess after producer feedback

- If notebook producers ask for `_repr_html_` / `__rich__`, that
  is the entry condition for designing one — not a default.
- If a producer needs structured artifact detail in the summary,
  treat it as a request to *enlarge `iter_artifacts()` ergonomics*,
  not as a request to grow the summary into the artifact plan.
- If a producer needs to *suppress* sections in `.to_text()`,
  consider adding a `sections=` kwarg with a small whitelist —
  but only after that producer is named.

### Skip

- A summary-formatter framework (`registry of formatters`,
  pluggable section handlers, …). Out of scope; if more than one
  text format ever lands, design then.

---

## See also

- [`builder_api_mvp.md`](builder_api_mvp.md) — the broader builder
  spec; the upload-class section currently lists demo-local helpers
  that this design replaces.
- [`builder_api_stability.md`](builder_api_stability.md) — the
  public-beta surface and deprecation policy that governs the
  stability layering in §7.
- [`parser_validation_boundary.md`](parser_validation_boundary.md)
  — the layering the summary lives within; the summary is purely a
  builder-layer surface and does not reach into parsers or adapters.
- [`adapter_authoring_quickstart.md`](adapter_authoring_quickstart.md)
  — the producer-facing quickstart; §8 names ``summary()`` and
  ``emission_diagnostics()`` as the recommended pre-upload
  inspection surface.
