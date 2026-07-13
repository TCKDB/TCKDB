# The Story of a Datapoint

*A narrative guide to how data lives and moves inside TCKDB.*

This is the "book" version of the backend: instead of listing modules,
it follows one contribution — a computed rate coefficient for the
hydrogen abstraction **n-butanol + OH → C4H8OH· + H2O** — from the
moment a researcher finishes their quantum chemistry jobs to the moment
a stranger on the other side of the world queries the result and can
trace every number back to the log file that produced it.

For the terse, `file:line`-anchored version of the same material, read
[`system_flow.md`](system_flow.md). For the vocabulary, read
[`core_concepts.md`](core_concepts.md). This document trades precision
for narrative: it is meant to be read once, start to finish, like a
short book.

---

## Chapter 1 — The problem the database is shaped around

Computational chemistry produces numbers that look final but aren't.
A "heat of formation of n-butanol" is not a fact; it is the output of
a long chain of choices — which conformer was used, which level of
theory, which frequency scale factor, which bond-additivity
corrections, whether the hindered rotors were treated as such or left
as harmonic oscillators. Two careful researchers can publish values
that differ by several kJ/mol, and both can be *right given their
choices*.

Most existing collections (group-additivity databases, thermodynamic
tables, mechanism libraries) store only the final number and a citation.
When two values disagree, there is nothing to arbitrate with. When a
better method comes along, old entries are silently overwritten. When a
mistake is found, its descendants cannot be traced.

TCKDB is designed as a reaction *against* that failure mode. Its whole
architecture follows from one taxonomy, applied without exception:
**every table in the database plays exactly one of four roles.**

| Role | Question it answers | Behavior |
|---|---|---|
| **Identity** | *What is this thing?* | Deduplicated — one row per chemical concept, reused across uploads |
| **Provenance** | *How was this produced?* | Append-only — every job, every observation is a new row, forever |
| **Result** | *What number came out?* | Append-only — new values never overwrite old ones |
| **Curation** | *How much should we trust it?* | An overlay — review state lives beside the science, never inside it |

Hold onto that table. Everything in the following chapters — why
species are deduplicated but calculations never are, why there is no
"preferred value" column anywhere, why deleting is impossible but
deprecating is easy — falls out of keeping those four roles separate.

---

## Chapter 2 — A contribution is born

Our researcher — call her Dana — has finished an ARC project. On her
cluster sit Gaussian and Orca output files: conformer searches for
n-butanol and the abstraction transition state, geometry
optimizations, frequency jobs, a few CCSD(T)-F12 single points, 1D
hindered-rotor scans, and an Arkane run that assembled all of it into
thermochemistry and a fitted rate coefficient.

None of that goes into TCKDB as files-first. TCKDB ingests **structured
payloads**: JSON documents that carry the *scientific content* — SMILES
strings, XYZ coordinates, energies in fixed units, frequencies, fit
parameters — plus, optionally, the raw log files as attached
*artifacts*. Dana has three ways to build that payload:

1. **The ARC ingestion script** (`backend/scripts/arc_ingestion/`)
   reads her ARC project directory and emits a complete
   "computed-reaction" payload automatically.
2. **The Python client** (`clients/python/`, package `tckdb-client`)
   offers builder helpers for constructing payloads by hand in a
   notebook.
3. **Raw JSON** against the OpenAPI schema, for anyone scripting in
   another language.

One rule governs every payload, and it is worth pausing on because it
shapes the entire write path: **upload payloads never contain database
IDs.** Dana does not say "attach this thermo to species #4172". She
says "this thermo belongs to the species with SMILES `CCCCO`, charge
0, multiplicity 1". Whether that species already exists in the
database is *not her problem* — resolving content to identity is the
server's job. This is what makes contributions portable: the same
payload can be replayed against an empty lab database or the hosted
community instance and mean the same thing in both.

Within a payload, pieces refer to each other by **local string keys**:
the thermo block says its source calculation is `"sp_ccsdt_conf1"`,
and a calculation with `key: "sp_ccsdt_conf1"` is declared elsewhere
in the same document. The payload is a self-contained little world;
the database will translate it into its own terms on arrival.

Dana authenticates with an API key (minted from her human login
session — keys can never mint other keys) and POSTs to
`/api/v1/uploads/computed-reaction`. Her client library stamps the
request with an **idempotency key**, so if her connection drops and
she retries, the server recognizes the duplicate and returns the
original outcome instead of ingesting twice.

---

## Chapter 3 — Every write opens a submission

The first thing the server does — before a single scientific row is
written — is open a **submission**. This is the ingestion invariant:
*no write happens outside a submission.* Every upload route follows
the same three beats:

```
open_upload_submission()  →  persist_*_upload()  →  mark_upload_ingested()
```

A submission is the reviewable unit of contribution: "Dana uploaded
this batch of records, together, at this time, with this client". It
starts at `status=pending` with a review policy of `under_review`, and
that is deliberately *not* a judgment — it is simply the initial state
of anything a curator has not yet looked at.

Two consequences of the submission wrapper are easy to miss and worth
stating:

- **Atomicity.** If ingestion fails halfway through Dana's bundle, the
  whole scientific write rolls back — but the *failure itself* is
  recorded in a separate transaction, so the audit trail shows a failed
  submission rather than nothing at all.
- **A 201 response does not mean the data is live.** It means the data
  was ingested and is now sitting in the review queue. Anonymous
  readers, by default, see curated data; Dana's records become broadly
  visible when a curator approves them. Successful ingestion is a
  technical statement; approval is a scientific one. The database
  never confuses the two.

Large contributions can take the **asynchronous path** instead: the
payload is accepted immediately as a queued job (the submission is
opened at accept time, so the contribution is auditable from the
moment it enters the building), and a background worker — claiming
jobs with `SELECT … FOR UPDATE SKIP LOCKED`, so multiple workers never
collide — runs *exactly the same persistence workflow* later. One code
path for the science, two doors into it.

---

## Chapter 4 — Identity: the database decides what things *are*

Now the workflow (`app/workflows/computed_reaction.py`) begins
translating Dana's payload, and the first job is identity resolution.

### Species

Dana's payload says `CCCCO`. The species-resolution service
canonicalizes that SMILES with RDKit and asks: *do we already know this
`(canonical_smiles, charge, multiplicity)` combination?* If yes — and
after a few years of community uploads, the answer for neutral,
closed-shell n-butanol is certainly yes — her contribution attaches to
the existing `species` row. If no, one is created. Either way, **there
is exactly one neutral, closed-shell n-butanol in the database**, no
matter how many people have uploaded it, in how many notations. An
InChIKey is also derived and stored for cross-notation search, but it
is not part of the dedup key (DR-0031) — see below.

One level down sits the `species_entry`: a specific *scientific form*
of the species — this stereochemistry, this electronic state, this
isotopologue, this stationary-point kind. The species answers "which
molecule?"; the entry answers "which version of it are you doing
science on?". Dana's ground-state, stereo-unspecified n-butanol
resolves to one entry; a deuterated or excited-state study would
resolve to sibling entries under the same species.

This two-level split repeats across the schema like a motif:
`chem_reaction` (the bare reactants→products identity, elementally
balanced and deduplicated on a stoichiometry hash) vs `reaction_entry`
(that reaction instantiated with specific species entries);
`transition_state` (the saddle-point concept for a reaction channel)
vs `transition_state_entry` (a concrete candidate saddle with a
geometry and a status: guess → optimized → validated → rejected).

Identity resolution is where near-collisions are handled once,
centrally, instead of by every client differently. It is also — being
the deduplication point — where the database's chemical opinions live.
Standard InChIKey alone cannot distinguish spin states (singlet vs.
triplet CH₂) and over-merges some tautomers (2-pyridone vs.
2-hydroxypyridine) — which is exactly why identity is keyed on
`(canonical_smiles, charge, multiplicity)` rather than InChIKey (see
DR-0031); InChIKey is retained only as a non-unique, cross-notation
search index. The architecture localizes chemistry-identity questions
like this: fixing identity logic is a change to one service, not to a
million stored results.

### Levels of theory, software, literature

The same resolve-or-create dance happens for the supporting cast.
"CCSD(T)-F12a/cc-pVTZ-F12 in Molpro 2023.2" resolves to a
`level_of_theory` row (hashed on its content, addressed by a stable
`lot_…` ref) and a `software_release` row. A cited paper resolves,
via DOI metadata lookup, to a `literature` row with linked authors.
None of these are ever duplicated; all of them are ever-growing
shared vocabularies that uploads point into.

---

## Chapter 5 — Provenance: the calculation record

Identity says *what* Dana studied. Provenance says *what she did* —
and unlike identity, provenance is **never deduplicated**. If two
people run the same optimization on the same geometry, that is two
facts about the world, and the database keeps both.

The hub is the `calculation` table: one row per ESS job, recording
type (`sp`, `opt`, `freq`, `scan`, `irc`, `composite`, …), the level
of theory, the software release, the input geometry, and the parsed
execution parameters. Around the hub, per-type result tables carry the
numbers: single-point energies (in hartree — fixed units, always),
optimization convergence, frequencies with a per-mode table (value,
reduced mass, IR intensity, symmetry label), scan tables that store
the full 1D/ND potential energy surface point by point, IRC profiles,
NEB paths.

Three provenance mechanisms deserve their own paragraphs:

**Geometries are content-addressed.** Every coordinate set is hashed;
the same XYZ uploaded twice becomes one `geometry` row referenced
twice. Geometries link calculations together physically: this freq
job's input geometry *is* that opt job's output geometry — the same
row, not a copy.

**Calculations form a DAG.** A frequency calculation can point at the
optimization that produced its geometry; a composite energy points at
its component jobs; Arkane-derived products point at everything they
consumed. These edges are *opportunistic enrichment* — a calculation
without them is still valid — but when present they are what makes a
result reproducible *from inside the database*, without opening a
single log file.

**Raw files ride along as artifacts.** Log files, and optionally
checkpoint files, are stored in S3-compatible object storage,
SHA-256-hashed, size-capped, and linked to their calculation. The
database stores the parsed, queryable numbers; the artifact preserves
the unparsed truth for the auditor who wants to check the parsing.

Alongside the numbers, the schema stores *quality evidence*: SCF
stability results (with the disciplined convention that *no row means
not checked* — absence of evidence is never silently converted into
evidence of absence), T1/D1 wavefunction diagnostics, and a geometry
validation record that checks graph isomorphism between the claimed
species and the actual optimized structure — the guard against the
classic silent failure where an optimization walked to a different
isomer than the uploader intended.

---

## Chapter 6 — Conformers: an identity for shapes

n-Butanol has dozens of low-lying conformers, and Dana's ARC run
found many of them. Here the identity/provenance split earns its keep
in miniature.

Every conformer Dana observed becomes a `conformer_observation` —
provenance, append-only: *this geometry, from this calculation, at
this level of theory*. But the database also wants to know when two
observations — hers at B3LYP, someone else's at ωB97X-D — found *the
same conformer*. That shared identity is the `conformer_group`,
and observations are matched into groups by **torsional basin
fingerprints**: discretize each rotatable dihedral into its basin, and
two geometries with the same basin signature are the same conformer in
the chemically meaningful sense, even if their coordinates differ in
the third decimal.

The rule that keeps this honest: cross-method consistency lives at the
group level, never on an observation. Nobody edits an observation to
say "this matches"; the group *is* the statement of matching. And
because assignment schemes are versioned, a future, smarter matching
algorithm can re-group old observations without falsifying history.

---

## Chapter 7 — Products: statmech, thermo, kinetics, networks

With identity and provenance in place, the workflow persists what
most users actually came for — the **scientific products**. These are
result tables: append-only, fixed-unit, provenance-linked.

**Statmech** is the interpretation layer between raw frequencies and
thermodynamics: external symmetry number, point group, rotor
treatments (rigid-rotor harmonic-oscillator through 1D and ND hindered
rotors), which frequency scale factor was applied — pinned to a
first-class, literature-sourced `frequency_scale_factor` row, with the
careful convention that a NULL factor means *unknown* and 1.0 means
*explicitly unscaled*. Each torsion links back to the scan calculation
whose PES justified its treatment.

**Thermo** stores the quantities modelers consume — ΔfH°298, S°298,
Cp/H/S/G at temperature points, fitted NASA polynomials — each row
carrying role-tagged links to its source calculations. Energy
corrections get the full two-layer treatment: a *reference library* of
correction parameters (atom energies, spin-orbit constants,
bond-additivity corrections, each versioned and citable) and an
*applied-correction* record that itemizes every term — "6 × C–H ×
(−0.11 kJ/mol) = −0.66" — so a corrected energy is an auditable sum,
not a mystery delta.

**Kinetics** stores rate coefficients: (modified) Arrhenius parameters
with enum-backed units, temperature validity ranges, per-parameter
uncertainties (with multiplicative vs additive semantics made
explicit), tunneling treatment, and role-tagged source calculations —
Dana's k(T) points at the reactant energies, the TS energy, the
frequency job, and the IRC that validated the saddle.

**Networks** handle pressure dependence: wells, bimolecular source and
sink channels, master-equation solves (with bath gas, energy-transfer
parameters, and solver provenance), and the fitted k(T,P) surfaces
(Chebyshev, PLOG) that each solve produced — every fit permanently
tied to the physical model that generated it.

The through-line of all four: **a product is never just a number. It
is a number plus the complete, machine-readable argument for it.**

---

## Chapter 8 — Curation: trust as an overlay

Dana's records now exist, linked to her submission, all in state
`under_review`. Enter the curator.

The curation layer is the fourth bucket, and its design rule is
absolute: **curation never mutates science.** A review changes how
much the database *believes* a record, never what the record *says*.

- Each record carries a `record_review` with a guarded state machine:
  `not_reviewed ↔ under_review ↔ approved / rejected / deprecated`.
  A contributor cannot approve their own records.
- Nothing is ever deleted. A record found wanting is *rejected* or
  *deprecated* — it drops out of default reads but remains in the
  historical record, still traceable, still citable by whatever
  depended on it.
- A **trust fragment** is computed deterministically at read time from
  evidence — review status, provenance completeness — rather than
  stored. Trust is a *derived* quantity; there is no column anyone can
  quietly edit.
- Optional machine assistance (automated rubric checks, LLM prechecks)
  writes its verdicts as append-only *advisory* rows. The machinery is
  deliberately firewalled: it can flag, it can annotate, it can never
  flip a review status or touch public output.

The curator inspects Dana's submission as a unit — the species, the
conformers, the calculations, the thermo, the kinetics, all linked to
one submission — checks the diagnostics the schema surfaced for
exactly this moment (did the TS have exactly one imaginary frequency?
does the geometry match the claimed species? is the T1 diagnostic
sane?), and approves.

---

## Chapter 9 — Reading: the best answer, decided at query time

Months later, a modeler in another group — call him Idris — needs
thermo for n-butanol. He hits the public read API:

```
GET /api/v1/scientific/thermo/search?smiles=CCCCO&collapse=first
```

What happens next is the design's quietest radical choice. The
database does **not** look up a stored "preferred value" — no such
flag exists anywhere. Instead, the read service gathers every
candidate thermo record for that species entry and *sorts* them, at
query time, by an explicit, documented policy: covers the requested
temperature range → smallest extrapolation → highest review status →
most complete evidence → newest. `collapse=first` returns the winner;
`collapse=all` returns the whole ranked field, so Idris can see the
runners-up and disagree with the ranking if his use case warrants it.

Why go to this trouble? Because *"best" is a policy, not a property.*
Stored preference flags rot: every new upload would demand re-curation
of old flags, and yesterday's blessing silently outlives its
justification. A read-time sort is always current, always explainable,
and always reproducible — the response can *echo the policy it used*.

Everything Idris receives is addressed by **public refs**
(`species_…`, `thermo_…`, `lot_…`) — stable, loggable handles — never
integer primary keys, which are an internal concern and hidden from
the public surface. And because reads default to
`min_review_status=approved`-class filtering, anonymous users see
curated science unless they explicitly opt into drafts.

From his one result, Idris can walk the chain the previous chapters
built: thermo → its role-tagged source calculations → each
calculation's level of theory, software release, parameters, input and
output geometries, artifacts (down to the SHA-256 of the original
Gaussian log) → the conformer observation and basin it belongs to →
the itemized energy corrections and the literature source of the
frequency scale factor. If he thinks Dana's number is wrong, he does
not have to trust or refute a bare value — he can inspect the actual
argument, re-run any link of it, and upload his own version, which
will live *alongside* hers, both permanent, both traceable, ranked at
read time by the evidence.

That closed loop — content-only writes in, identity resolved
centrally, provenance and results appended forever, trust layered on
top, selection computed at read time, everything addressable by
stable public refs — is the entire system. Every module in
`backend/app/` is an implementation detail of one of those clauses.

---

## Chapter 10 — The ecosystem around the core

A few supporting structures complete the picture:

- **Bundles and offline contribution.** A contribution can be packaged
  as a bundle, dry-run validated without writing, and replayed later —
  the same content-only payload philosophy makes contributions
  portable across deployments (a lab's private instance today, the
  community host tomorrow).
- **One app, many deployments.** The same codebase runs as a laptop
  dev instance, a lab's self-hosted single node (there is a
  982-line operator runbook, systemd units including a nightly backup
  timer, and a startup guard that refuses to boot a publicly-exposed
  instance with unsafe settings), or the hosted community database.
  Docker currently provides the data plane (PostgreSQL with the RDKit
  cartridge, MinIO for artifacts); the API itself runs from a conda
  environment — API containerization is the next packaging milestone.
- **Migrations as history.** The schema itself follows the append-only
  philosophy: a baseline Alembic revision plus layered revisions, with
  a written policy for what may change in place (only tables that have
  never held real data) and what must never (everything else).
- **Clients.** `tckdb-client` (HTTP, retries, idempotency, typed
  errors, builders) is the supported push path; ARC integration reads
  ARC's own output files rather than coupling to ARC's internals. The
  read API is deliberately client-agnostic: plain HTTP + JSON, public
  refs, OpenAPI-documented.

---

## Epilogue — What the shape of the thing means

Strip away the chemistry and TCKDB is a particular answer to a general
question: *how do you run a database of contested, evolving,
expensive-to-produce scientific claims?* Its answer:

1. **Separate what a thing is from what was measured about it** — so
   agreement is discoverable and disagreement is representable.
2. **Never overwrite** — so the record of science is a history, not a
   snapshot.
3. **Make trust explicit, derived, and separate** — so belief can
   change without the evidence changing.
4. **Decide "best" at the moment of asking** — so curation debt can't
   accumulate inside the data.
5. **Accept content, not references** — so contributions are portable
   and the server owns identity.

Each rule costs something — more tables, more resolution logic,
reads that sort instead of point-lookup — and each buys something a
community science database cannot live without. The gaps that remain
(they are real: unstorable Hessians, spin-state identity, falloff
kinetics, bulk export — see the current backend assessment) are
gaps *within* this architecture, not flaws *of* it: every one of them
has a natural home in one of the four buckets, waiting for a table
that respects the same rules.
