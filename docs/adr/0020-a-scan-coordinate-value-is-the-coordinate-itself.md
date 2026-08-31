# A scan coordinate value is the coordinate itself, in its own unit

**Status: accepted 2026-08-31.** Supersedes
[0019](0019-a-scan-point-records-the-producers-axis.md), whose central argument
does not survive contact with the question it was answering. Constrained by
[0008](0008-validation-tiers-definitions-block-expectations-warn.md) and
[0011](0011-atom-mapping-is-declared-not-inferred.md). The correction of the 46
already-deposited series and the conformance check that finds non-conforming
deposits are decided here and implemented separately.

Yesterday's decision recorded that `calc_scan_point_coordinate_value.coordinate_value`
carries whatever axis the depositing producer used, and that TCKDB documents the
relationship rather than enforcing one. On the deposited corpus that axis is a
sweep *relative* to the first scan point, with the absolute dihedral of the
starting geometry held separately in `calc_scan_coordinate.start_value`.

That decision was wrong, in a way worth writing down because the error is a
recurring one: **it described a producer and called the description a contract.**

## Why 0019 fails

**Its central argument is circular.** 0019 declined to normalise at ingest
because "the server cannot distinguish a relative sweep from a stale absolute
anchor without guessing". That is true only in the absence of a declared
meaning. If TCKDB says what the column holds, there is nothing to infer — a
deposit either conforms or it does not. The absence of a contract was used as
the reason not to have one.

**Its convention does not generalise past dihedrals,** which is fatal for a
column that four `coordinate_kind` values share:

- A **bond angle** occupies [0, 180] and *reflects* at its endpoints rather than
  wrapping. `start_value + coordinate_value` runs past 180° and produces a number
  that is not an angle.
- A **bond** is a distance in Ångström. There is no branch cut, `mod 360` is
  meaningless, and the transformation that produces the relative form in the one
  producer we have is a degrees-specific wrap — nonsense applied to a length.
- An **improper** dihedral has no single convention across codes: in some it is
  the out-of-plane angle of one atom from the plane of three, in others a
  proper-style torsion about a central atom. TCKDB stores no field telling them
  apart, so a relative form anchored to an unstated definition is anchored to
  nothing.

So the relative convention is not a general way to record a scanned coordinate.
It is an artifact of one parser's handling of one code's print format, which
0019 mistook for the shape of the problem.

**And the evidence 0019 cited for it was partly hollow.** The record leaned on
`end_value == start_value + 360` holding across all 46 series. That identity is
computed by the producer as `start + step_size × (step_count − 1)`. It is
arithmetic, not observation, and says nothing about what `coordinate_value`
means. What does hold — verified by recomputing dihedrals from the stored
Cartesian coordinates of 78 scan points, max deviation 1.5 × 10⁻⁴° — is the
relative reading itself. The finding was right; one of its three legs was not
evidence.

## The decision

**`coordinate_value` is the value of the internal coordinate at that sampled
point, expressed in that coordinate's own unit** — degrees for `angle`,
`dihedral` and `improper`, Ångström for `bond`. It is not a displacement, not an
offset, and not relative to anything.

`start_value` and `end_value` revert to what their names say: the requested
extent of the scan grid, input metadata beside `step_count` and `step_size`.
Neither is an anchor, because nothing needs anchoring.

A periodic coordinate **may continue past 360°** where doing so keeps a sweep
monotone, and readers take `mod 360` for the physical angle. This is not a
second convention: 419.867° and 59.867° are the same value of the same
coordinate, and the choice between them is presentational. Storing the
continuation preserves something real — which turn of a relaxed, path-dependent
scan a point belongs to, and the fact that the first and last points of a full
sweep are the same geometry deposited twice.

**Producers conform.** A producer whose program prints a relative sweep converts
before depositing; it necessarily holds the anchor, since it computed the sweep
from it. TCKDB does not accept the transformation and then carry it forever, and
it does not adopt a producer's internal representation because that producer
happens to be the only one so far.

This is the rule [0011](0011-atom-mapping-is-declared-not-inferred.md) already
runs on, applied one level up. There, TCKDB refuses to *infer* a scientific claim
and requires the depositor to state it. Here, TCKDB refuses to infer what a
deposited number *means* — and the way to stop inferring is not to record the
ambiguity, as 0019 chose, but to remove it.

## What this costs, and who pays

**Every deposited scan series is now non-conforming** — 46 of them, all
one-dimensional dihedral scans in degrees from a single producer. They hold
`0, 8, 16 … 360` where the contract asks for the dihedral itself.

The obvious repair is unavailable. TCKDB stores uploaded artifacts verbatim
([0004](0004-store-artifacts-verbatim-gate-raw-log-access.md)), which would
normally allow a corpus to be re-derived under a new contract without returning
to the machines that produced it — but **scan calculations have no artifacts**.
Measured against the hosted instance on 2026-08-31: `opt` 232, `freq` 165, `sp`
164, `scan` **0**. The raw logs for exactly the calculation type that needs them
were never deposited.

So the correction is made in place, and this record is where that is declared.
Three facts make it defensible:

1. **It is exact and invertible.** `coordinate_value := start_value +
   coordinate_value` loses nothing, because `start_value` is retained.
2. **It touches no accepted science.** All 50 scan calculations are
   `not_reviewed` at quality `raw` (measured 2026-08-31). Nothing
   [0003](0003-freeze-ever-approved-science.md) freezes is in scope, and no
   supersession edge under
   [0015](0015-a-repair-to-accepted-science-is-declared-before-it-is-made.md) or
   [0018](0018-an-update-names-what-a-submission-owns-and-proves-it-unchanged.md)
   is required for a record no one has approved.
3. **It refuses to guess.** The migration recomputes the dihedral from each
   point's own stored geometry and converts a row **only** where the recomputed
   value confirms the converted one. A row whose geometry does not confirm it is
   left exactly as deposited and reported. A row with no geometry, no
   `start_value`, or an ill-conditioned quartet is not converted.

This is a rewrite of append-only result rows, and that invariant is real. It is
overridden here, once, narrowly, for unreviewed rows under a proof, because the
alternative is a flagship corpus that permanently violates the contract its own
database declares, with no route to fix it. **The exception is not a precedent:
a future non-conforming deposit is corrected by re-depositing, not by migrating.**

## Enforcement

A conformance check compares `coordinate_value` against the coordinate
recomputed from the sampled point's own geometry. It needs no anchor and no
producer-specific knowledge, which is the point — it asks the database's own
question, of any depositor.

Under [0008](0008-validation-tiers-definitions-block-expectations-warn.md) it
**warns**. A disagreement is evidence that something is wrong, but not which
thing: a mis-stated axis and a mis-attached geometry present identically, and
refusing the deposit would discard correct energies over an ambiguity TCKDB
cannot resolve alone.

Two limits belong on the record. Coordinates are deposited at finite precision
— six decimal places on the current corpus — which puts a floor of roughly
3.6 × 10⁻⁵° (1σ) on any recomputed dihedral, so the check's tolerance is derived
from deposit precision and the `1/(r sin θ)` conditioning of the quartet, never
fixed by hand. And where a quartet is near-collinear the dihedral is not a usable
coordinate at all; there the check reports **not checkable**, which is neither a
pass nor a failure, mirroring what the producing tools themselves do.

## What this does not decide

Whether a scan calculation should be required to deposit its artifact, which the
zero above makes an obvious question and a separate one. Whether `step_count`
should be renamed, having been found to hold a *point* count where its name
promises steps. Whether `resolution_degrees`, an `Integer` column fed by a float
wire field, should be retyped — a real defect, on a deployed table, deserving its
own revision. And whether an unanchored scan with no geometry should be
depositable at all, which 0019 answered by accepting it and this record does not
revisit.
