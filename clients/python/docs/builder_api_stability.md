# `tckdb_client.builders` — API stability & deprecation policy

Status: **public beta / preview** as of `tckdb-client` 0.22.0.

The thin HTTP client at `tckdb_client.TCKDBClient` is
production-stable. The builder layer documented here is in public
beta: recommended for early adopters who want a typed, validated
upload surface, with the known forward-compat gaps laid out below.
The API may still gain optional fields and new diagnostic codes
before v1; the **shape** of what is currently exposed will not break
without a deprecation period.

This document is the source of truth for what is public, what is
internal, and what may change.

---

## 1. Public beta surface

The following imports are part of the public beta API. Names not on
this list are internal and may be moved, renamed, or removed at any
time without a deprecation period.

### Top-level (always importable from `tckdb_client`)

```python
from tckdb_client import (
    TCKDBClient,                       # thin HTTP client (production-stable)
    TCKDBResponse,
    # Error hierarchy — production-stable
    TCKDBError,
    TCKDBHTTPError,
    TCKDBAuthenticationError,
    TCKDBForbiddenError,
    TCKDBValidationError,
    TCKDBConflictError,
    TCKDBConnectionError,
    TCKDBIdempotencyConflictError,
    # Idempotency helpers — production-stable
    make_idempotency_key,
    validate_idempotency_key,
    # Replay machinery — production-stable
    replay_bundle,
    ReplaySummary,
    ReplayFailure,
    ClientFactory,
)
```

### Builder subpackage (public beta)

```python
from tckdb_client.builders import (
    # Identity
    Species,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    # Computation
    Calculation,                       # .opt / .freq / .sp factories
    # Reaction side
    ChemReaction,
    TransitionState,
    Kinetics,                          # .modified_arrhenius factory
    # Scientific products
    Thermo,                            # .scalar / .nasa / .points
    Statmech,
    Transport,
    # Top-level upload objects
    ComputedSpeciesUpload,
    ComputedReactionUpload,
    # Artifacts (two-phase upload)
    Artifact,
    PlannedArtifactUpload,
    ARTIFACT_KINDS,
    # Upload introspection
    CalculationEntry,
    # Diagnostics
    Diagnostic,
    DIAG_CODES,
    # Local-validation error
    TCKDBBuilderValidationError,
)
```

All other modules under `tckdb_client.builders.*` (e.g. `base.py`,
`validation.py`, `uploads.py` internals) are implementation detail.

---

## 2. Diagnostic-code stability

Each upload object's `emission_diagnostics()` returns a list of
`Diagnostic(level, code, message, path)` records. The **`code`** field
is the load-bearing public contract:

- `code` values are **stable strings** for the duration of the public
  beta. Renaming an existing code is a breaking change and follows
  the deprecation policy in §4.
- New codes may be added in any minor release (additive change).
- `message` is human-readable and may be reworded without notice.
- `path` is a logical builder path (e.g. `species_transport[CH4]`).
  Its shape (string with a dotted/bracketed accessor) is stable;
  exact text is not.
- `level` is `"info"` or `"warning"`; the set of levels will not
  shrink in beta. Adding a new level value would be a breaking
  change.

The currently defined codes are exported via `DIAG_CODES`:

| Attribute | Token value |
|-----------|-------------|
| `TRANSPORT_NOT_EMITTED_IN_COMPUTED_SPECIES_BUNDLE` | `transport_not_emitted_in_computed_species_bundle` |
| `TRANSPORT_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE` | `transport_not_emitted_in_computed_reaction_bundle` |
| `THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE` | `thermo_source_calculations_not_emitted_in_computed_reaction_bundle` |
| `ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE` | `artifact_upload_requires_second_phase` |

These all reflect known schema gaps. When the backend bundle schemas
grow the matching fields, the corresponding diagnostics will simply
stop being emitted — the **codes remain valid** (so producer match
logic does not break), they just don't appear in any new uploads.

---

## 2.1 Upload introspection helpers

Both upload classes expose four iteration / preview helpers
introduced in 0.24:

- `upload.iter_calculations(*, with_artifacts_only=False)`
- `upload.iter_calculation_entries(*, with_artifacts_only=False)`
  — yields `CalculationEntry(bucket, species, calculation)`
- `upload.iter_artifacts()` — yields `(Calculation, Artifact)` pairs
- `upload.artifact_plan_preview(*, starting_calculation_id=1000)`

These are public beta. The **iteration order** is part of the
contract: TS bucket first on the reaction side, then species
buckets in `reaction.unique_species()` order; for the species
upload, every calc is in the single species bucket.

`artifact_plan_preview` is for offline demos, CI fixtures, and
producer debugging. **The IDs it returns are deterministic
synthetic integers, not real server-assigned calculation primary
keys**. Code that needs real IDs must wait for the live
`client.upload(upload)` result and call `upload.artifact_plan(result)`.

## 3. What may still change before v1

The following are explicitly *not* frozen:

- **Optional builder kwargs.** New optional fields may be added on
  any builder class (`Thermo`, `Statmech`, `Transport`, `Calculation`,
  …) in minor releases without notice. Required-field changes follow
  §4.
- **Conformer model.** The builder emits one scientifically meaningful
  conformer per species upload, by design. This is policy, not a
  deferred feature — see
  [`conformer_semantic_boundary.md`](conformer_semantic_boundary.md)
  for the full rationale and the list of API shapes the project
  rejected. Producers that have a real scientific need to submit
  several records for the same species do so as independent
  submissions, not as a candidate list bundled into one upload.
- **Artifact upload helpers.** Two-phase artifact uploads via
  `Calculation.add_artifact(...)` are designed in the spec but not
  yet implemented. Their API may shift during implementation.
- **Per-endpoint emission flags.** When backend bundle schemas grow
  the currently-missing `transport` and `species_thermo.source_calculations`
  fields, the upload assemblers will flip `allow_source_calculations=True`
  (or the equivalent emission switch) at the call site. Builder
  callers are not affected; only the matching diagnostics stop
  appearing.
- **Wire shape of emerging fields.** A field that today is
  "accepted but not emitted" (Transport on either bundle endpoint,
  computed-reaction species_thermo source_calculations) may settle on
  its on-wire shape during the next backend schema iteration. Any
  builder field name that changes will keep the old kwarg as a
  deprecated alias for at least one minor release.
- **Frequency-scale-factor builder.** `Statmech.freq_scale_factor`
  is a `FreqScaleFactorRef` complex value on the backend; the builder
  doesn't expose it yet. When added it may displace the current
  `Statmech` field ordering — kwargs are unaffected because builders
  are constructed by keyword.
- **`StatmechTorsionInBundle` torsion list.** Same as above —
  hindered-rotor metadata is a defined backend shape that the builder
  doesn't surface yet.
- **Bundle-side `thermo.source_calculations` on computed-reaction.**
  The corresponding diagnostic
  (`thermo_source_calculations_not_emitted_in_computed_reaction_bundle`)
  will stop appearing once the backend `BundleThermoIn` adopts the
  `source_calculations` field.

---

## 4. Deprecation policy

Public-beta deprecations follow a single rule: **at least one minor
release of overlap**, with the old surface continuing to work and a
`DeprecationWarning` raised at use site.

- **Removing a public builder class or kwarg.** Keep it for at least
  one minor version after the deprecation lands; emit
  `DeprecationWarning` at first use; document the replacement in
  CHANGELOG and in the deprecated symbol's docstring.
- **Renaming a public symbol.** Add the new name *and* keep the old
  one as a `DeprecationWarning`-issuing alias for the same overlap
  window.
- **Renaming or removing a `DIAG_CODES` entry.** Add the new code;
  keep the old token producing the same diagnostic for the overlap
  window; document the migration in CHANGELOG.
- **Breaking a required-field type.** Treated like a rename — add a
  parallel form, deprecate the old, document the migration. A
  type-only widening (e.g. accepting `int` where only `float` was
  accepted before) is additive and not a deprecation.

Pre-v1, there is no SLA on the overlap window being longer than one
minor release. v1 will pin a deprecation cycle that matches semver
expectations for downstream producer packages.

---

## 5. Versioning expectations

`tckdb-client` follows semver with one practical clarification:

- **0.y.z (today).** Minor releases (`y`) bundle the next builder
  phase and may add new public symbols. Patch releases (`z`) are
  bug-fix-only and do not add public API. Builder field renames or
  removals follow §4.
- **1.0.0 (future).** Will lock the builder-layer public surface as
  documented here, switch the development-status classifier from
  `4 - Beta` to `5 - Production/Stable`, and adopt strict semver for
  the builder layer.

---

## 6. Server compatibility

The `tckdb-client` package sends `X-TCKDB-Client-Name` and
`X-TCKDB-Client-Version` headers on every request. Hosted servers may
enforce a minimum supported client version on write endpoints and
return `426 Upgrade Required` for older clients. The client itself
does not negotiate a server version — see the API key + server-info
endpoint for hosted instance discovery.

The builder layer's wire-shape contract is pinned by backend
**contract tests** under `backend/tests/client_builder_contract/`.
Any drift surfaces there before it reaches a user.

---

## 7. Not covered by this policy

- The thin HTTP client and error hierarchy follow their own
  production-stable contract — they are out of scope here.
- Backend schemas themselves are not part of the client's stability
  policy. The client tracks what the backend currently accepts; when
  the backend evolves, the client follows.
- Anything imported from `tckdb_client.builders.*` that is not in §1
  is internal and may move at any time.

---

## See also

- [`builder_api_mvp.md`](builder_api_mvp.md) — the full builder
  spec the surface in §1 enumerates.
- [`adapter_authoring_quickstart.md`](adapter_authoring_quickstart.md)
  — the producer-facing quickstart for adapter authors consuming
  the public-beta surface this policy governs.
