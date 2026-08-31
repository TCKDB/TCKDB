# A scan point records the producer's axis, not an absolute coordinate

**Status: accepted 2026-08-30; SUPERSEDED 2026-08-31 by**
[0020](0020-a-scan-coordinate-value-is-the-coordinate-itself.md), which
declares `coordinate_value` to be the coordinate itself and converts the
deposited corpus. The reasoning below is retained as history; its central
argument — that the axis cannot be normalised because the server cannot
infer which one arrived — is circular, since the inference is only
necessary while no contract exists. **Do not build against this record.**

**Original status: accepted 2026-08-30.** Recording the deposited axis was chosen over
normalising it at ingest; no schema change, no rewrite of the deposited corpus,
and no enforcement of the convention beyond documenting it. Verifying a
producer's anchor is named as future work and deliberately not built here.

A torsion scan rotates one internal coordinate through a full turn and records
the energy at each step. TCKDB stores the grid in `calc_scan_coordinate` and the
sampled points in `calc_scan_point` plus `calc_scan_point_coordinate_value`. What
it does not store — anywhere, in any form — is *what axis the per-point
`coordinate_value` is measured on*. The column is a bare float with a
`CoordinateUnit`, and until this decision nothing in the schema, the wire
contract, or the read API said whether `8.0` meant "the dihedral was 8°" or "the
scan had advanced 8° from wherever it started".

The two readings differ by up to a full turn, and on the deposited corpus they do
differ. Measured against the hosted instance on 2026-08-30: 46 scan series (50
scan-type calculations, four without a `calc_scan_result`), every one of them
one-dimensional, in degrees, at 8° steps, all produced by Gaussian via ARC. All
46 store points running `0, 8, 16 … 360` — a **relative** sweep, first point
exactly `0.0` in 46 of 46 — while `calc_scan_coordinate.start_value`
holds the **absolute** dihedral of the starting geometry (`59.867`, `300.563`,
`246.341`, …) and `end_value` is `start_value + 360` in all 46. The single series
whose two axes coincide is vinyl alcohol's, and only because its starting
dihedral happened to be `0.0`.

That was confirmed from geometry rather than deduced from column names. Every
point of every series carries a `geometry_id` — 46 of 46 series complete — so the
dihedral can simply be computed. For `calc_l43amz3thaubb5xoonwtgjmqlm` (1-nonene,
dihedral 7-8-9-25, `start_value = 59.867`), the angle measured from each point's
stored coordinates reproduces `(start_value + coordinate_value) mod 360` to three
decimals.

## Why this has been invisible

Nothing has broken, and the reason is worth stating because it is also the reason
the gap will not stay harmless.

There is one producer. Every scan in the database arrived through the upload API
from ARC, so the corpus is a monoculture and internally consistent by accident of
provenance rather than by contract. And the one in-repo consumer,
`backend/scripts/validation/arkane_statmech_roundtrip.py`, fits a Fourier rotor
potential, which is invariant to a phase offset — it reads the axis, converts to
radians, and gets the right barrier whichever convention it was handed.

Both properties are already fragile. `app/services/orca_parameter_parser.py`
parses ORCA's `SCAN B 11 16 = 3.245, 0.745, 126` directive and its
`RELAXED SURFACE SCAN RESULTS` table, and both yield **absolute** coordinates.
Nothing persists them today — `calculation_parameter_extraction` keeps only the
`parameters` — but wiring that path to the scan tables would put a second
convention into the same column with nothing to tell them apart. And any reader
that wants a real internal coordinate rather than the shape of a potential —
comparing two scans of the same torsion, locating a minimum in dihedral space,
plotting a potential against a geometry — is already reading the column wrong
today with no way to discover it.

## Why not normalise at ingest

The obvious repair is to convert everything to one convention at the write
boundary, as `_parse_scan_definitions` already does for ORCA's 0-based atom
indices, so that downstream code sees one axis and never asks. Three things
defeat it.

**The server cannot tell what arrived.** Given points `0 … 360` and
`start_value = 59.867`, a relative sweep and an absolute scan with a stale anchor
are the same bytes. Normalisation would need a heuristic — *if the first point is
zero and the anchor is not, assume relative* — which is precisely the guessing
[0011](0011-atom-mapping-is-declared-not-inferred.md) refuses for atom maps and
the ESS-extraction rule refuses for parsed values. The atom-index precedent does
not carry: that conversion is total, every 0-based index having a 1-based
counterpart, whereas axis normalisation is partial and fails exactly where the
producer was least explicit.

**It converts a tolerance into a rejection.** `start_value` is nullable with no
check constraint, so an unanchored scan is expressible — a rotor potential
digitised from a paper, a fitted torsional profile, any scan deposited without a
starting geometry. Normalise-or-reject makes those undepositable. Under
[0008](0008-validation-tiers-definitions-block-expectations-warn.md) an
unanchored scan is an *incomplete* record, not a false one: the energies are
still the energies, and only the axis label is missing. Refusing it would discard
correct science over metadata the depositor may not have.

**It would rewrite results.** The 46 deposited series are result rows, and result
tables are append-only. Backfilling them mutates recorded observations; not
backfilling leaves two conventions in the column permanently, which is the
outcome normalisation was meant to prevent. There is no version of ingest
normalisation that is both complete and non-destructive.

## The decision

**A scan point's `coordinate_value` is recorded as the producer parameterised
it. `calc_scan_coordinate.start_value` is the anchor, and the relationship
between them is documented rather than enforced.**

The convention now lives in `backend/schema_spec.md` §7.3, with the conversion
written out, the measured evidence behind it, and the two traps: convert to the
**unwrapped** form (`start_value + coordinate_value`, no modulus), because `mod
360` crosses the branch cut mid-sweep and destroys the monotonicity that makes
the points a path — which is why `end_value` is already stored unwrapped — and
note that the addition assumes a positive-direction sweep, which no stored field
asserts.

This is the smallest change that fixes the actual defect. The data is complete,
the API already serves `start_value` alongside the points, and every conversion a
reader needs is computable today from what is already returned. The entire
failure was that nobody was told.

## What is deliberately not built

**A stored axis marker.** A column declaring `relative` or `absolute` would be a
new field on a deployed table, and it would record a producer's claim about their
own data that nothing checks. It buys nothing over documentation until a second
producer exists.

**A derived absolute value on the read API.** Serving one would assert
`start + coordinate_value` is the right direction, and no check has verified that
the sweep is not reversed. An API that states an unverified fact is worse than
one that stays silent, so the derived field and the check that earns it should
land together or not at all.

**Verification of the anchor.** Two failure modes are detectable and neither is
detected: an anchor that disagrees with the geometry it claims to describe, and a
sweep stored in the direction opposite to the one it ran. Both are catchable by
measuring the dihedral at two points from their stored geometries and comparing
against `start_value` — two geometry reads per scan, once, in the checks layer.
Measuring on the *read* path was considered and rejected: coordinates live one
row per atom in `geometry_atom`, so a 200-point page would load thousands of
extra rows to recompute a number `start_value` already gives away. Under
[0002](0002-separate-reproducibility-from-trust.md) a declared anchor that
disagrees with stored evidence is a trust signal, not a read-time computation.

The trigger for building it is a second producer depositing scans, not a date. A
convention that one tool follows needs writing down; a convention two tools
follow needs enforcing.

## Consequences

Readers who have been treating `coordinate_value` as an absolute dihedral have
been wrong, and their results are offset by `start_value` — for the deposited
corpus, by anything up to a full turn. Nothing in the database changes, so
anything already computed from these scans has to be re-examined against the
documented convention rather than corrected by a migration. Rotor fits and
barrier heights are unaffected.

An unanchored scan remains depositable and remains uninterpretable in absolute
terms from the scan tables alone. Where its points carry geometries — as all
deposited points do — the axis is still recoverable by measurement, which is the
same escape hatch the future check would use.
