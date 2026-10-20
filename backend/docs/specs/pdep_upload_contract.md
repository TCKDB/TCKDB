# PDep upload contract v2

Status: required before the next production PDep network upload.

The `/uploads/networks/pdep` endpoint keeps its URL, but payloads must satisfy
the v2 scientific-integrity contract. Earlier payloads that omit these fields
are rejected; the backend does not manufacture missing scientific evidence.

## What v2 requires

- Every channel declares a `mechanism`, a machine token that says what evidence
  stands behind it. It defaults to `elementary`, so a payload that names its
  paths is unaffected. It is orthogonal to `kind`, which classifies the
  channel's macroscopic reaction type.
- An `elementary` channel supplies explicit `microreaction_paths`. A path names
  a `micro_reaction_key` and either the `transition_state_key` of the saddle
  point it goes through, or nothing at all — omitting it declares the path
  **barrierless / variational**, which is the honest description of a
  radical-radical association or a simple bond fission. Distinct pathways may
  share macroscopic endpoints, and one elementary reaction may proceed through
  several distinct saddle points (e.g. syn/anti conformers of one TS); that is
  expressed with several paths on **one** micro reaction, never with duplicate
  micro reactions of identical stoichiometry.
- A `well_skipping` channel supplies **no** paths, and says so. See
  "Well-skipping channels" below. A channel that omits its paths *without*
  declaring `mechanism: well_skipping` is still rejected: silence is never a
  declaration.
- A solve declares `kind`. A `computed` solve — the default, and the preferred
  form — is one whose master equation was solved here, and the three coverage
  rules below apply to it in full. A `reported` solve carries k(T,P)
  transcribed from a publication; it supplies `literature` and at least one
  `channel_kinetics` entry, is not held to the coverage rules because it holds
  none of those inputs, and returns a `reported_network_solve` upload warning.
  Anything a reported solve *does* supply is validated exactly as strictly.
  See ADR 0010.
- A computed solve gives a state energy for every state, and one
  forward/reverse barrier for every *saddle-point* channel path. A barrierless
  path carries no barrier — offering one would be a fabricated number.
- Both energies and barriers declare a machine-token `energy_zero_convention`
  and `correction_convention` (see `app/db/models/common.py`). Free text is
  rejected. `other` is the single escape hatch and requires `convention_note`.
- Barriers are **signed** relative to the declared zero and oriented by the
  channel (source → sink), not by the micro reaction's own written direction. A
  submerged entrance barrier is legitimately negative; only non-finite values
  are rejected.
- Energy-transfer rows on a computed solve declare a `scope`. `per_well` rows
  name a `(state_key, collider_species_key)` pair and must cover the full cross
  product of collisionally-stabilised well states and declared bath-gas
  colliders; one well's ⟨ΔE⟩down does not describe a five-well network. Since
  `b6e1d3a9c740` a run that specified a single `energyTransferModel` for the
  whole network — as Arkane and MESS inputs usually do — declares instead one
  `network_wide` entry naming no state and no collider, which is accepted and
  returns a `network_wide_energy_transfer_scope` warning. Mixing the two scopes
  in one payload is rejected. Duplicating a single value across the wells to
  make it look per-well is the fabrication ADR 0009 exists to stop.
- IRC validation evidence on a transition state is **optional but strongly
  recommended**. Depositing a TS without it succeeds and returns a
  `transition_state_missing_irc_evidence` upload warning. What is never accepted
  is incomplete evidence presented as passing: when participant→atom mappings
  accompany `passed: true` they must be 1-based and cover the entire TS atom set
  exactly once on *both* sides.
- Normal-mode-displacement ("nmd") evidence does not exist in TCKDB. Reading an
  imaginary mode's displacement vectors is a producer-side heuristic; the
  database stores only the reconstructed-path evidence an IRC calculation
  actually produces.
- TS partition functions are recorded as TS-owned `statmech` records, never as
  pseudo-species statmech.

## Well-skipping channels

A pressure-dependent network's most characteristic rates are the ones that no
single elementary step produces. `NH2 + NH2` recombines into energized `N2H4*`,
which dissociates to `H + N2H3` before collisional stabilization; the master
equation returns one phenomenological k(T,P) for `NH2 + NH2 → H + N2H3` even
though the pathway is two elementary steps through a well. These are the
chemically-activated, or **well-skipping**, channels. They have no saddle point
of their own, so requiring mechanistic attribution of every channel silently
discarded them — for the hydrazine network, 15 of 21 channels, leaving only the
6 that essentially reproduce the high-pressure limit.

The DB never forbade them: `network_channel_microreaction` is a separate table
and a channel with zero rows there has always been representable. The upload
contract was the only thing in the way.

v2 therefore accepts them, on three conditions:

1. **Declared, never inferred.** The channel sets
   `mechanism: "well_skipping"`. There is no `unknown` or `other` member on the
   enum, because an escape hatch here would be indistinguishable from "the
   producer omitted the paths".
2. **No paths.** A well-skipping channel must supply an empty
   `microreaction_paths`. If a single elementary step does join the endpoints,
   the channel is elementary and must name it. It carries no `channel_barriers`
   entry either, for the same reason a barrierless path carries none: there is
   no barrier to state.
3. **Verified against the topology.** The declaration is checked, not trusted.
   `NetworkPDepUploadRequest.validate_well_skipping_channels` requires that the
   endpoints are *not* directly connected by any declared micro reaction, and
   *are* connected by a chain of declared micro reactions every intermediate of
   which is a `well` state. Bimolecular and termolecular configurations are
   reservoirs in the master equation — flux reaching one has separated into
   products — so they cannot be intermediates on a single chemically-activated
   channel. The backend still manufactures no evidence; it reads the network
   topology the producer already supplied and confirms it supports the claim.

`mechanism` is stored on `network_channel` (revision `d5b1a7c3e9f4`,
`server_default 'elementary'`) rather than derived from an empty child
collection, and is always present on the channel read surfaces. "This rate is a
multi-step ME channel" is a scientific claim about the channel, not an absence
of data, and a reader must be able to tell it apart from an incomplete deposit
without consulting the contract version that wrote the row. The backfill is
unambiguous: `c1d2e3f4a5b6` refuses to run unless `network_channel` is empty, so
every row this revision can reach was written under v2, which required at least
one path per channel.

Deliberately **not** recorded: *which* wells a given well-skipping channel
traverses. The master equation does not attribute its phenomenological flux to
one route, so naming a route would be a fabricated attribution. The traversal is
recoverable from the network topology, which is stored in full.

## Rollout

This is a breaking writer contract. ARC or other producers must upgrade
atomically with the backend before sending PDep payloads.

The contract is **not** yet published as a versioned module in the
`tckdb-schemas` package — `schemas/python/tckdb-schemas/` carries no PDep upload
schema, and the 0.11.0 bump covers the statmech validators only. Until a PDep
module ships there, the authoritative definition is
`backend/app/schemas/workflows/network_pdep_upload.py`, mirrored in the OpenAPI
document served by the deployment.

`backend/scripts/pdep_ingestion` builds a conforming v2 payload from an Arkane
pressure-dependence run and is the reference producer.

## Existing production data

The deployed database **does** hold pre-v2 PDep rows (a hydrazine network with
Chebyshev and PLOG kinetics, seeded 2026-07-15). Migration `c1d2e3f4a5b6`
therefore refuses to run against it and raises a `RuntimeError` naming the
offending tables before applying any DDL.

Those rows cannot be transformed: an unscoped ⟨ΔE⟩down does not record which
well it applied to, and a v1 channel has no producer-visible identity. The
resolution is **export, delete, re-upload from the source calculations** — never
a guessed backfill. The operator runbook is
`backend/docs/deployment/migrations.md`, section "Stage 2 PDep re-upload".

Pi deployment additionally requires the editable schema-package metadata refresh
described in that runbook, followed by backend migration and API restart.

## Known gaps

Recorded deliberately: each is a limitation a consumer can hit, with its
scientific consequence, so it is not rediscovered as a surprise.

### Isotopomers are not distinguished from isotopologues

`assert_geometry_isotopes_match_identity`
(`backend/app/services/species_resolution.py`) compares only the **multiset**
of isotope substitutions between the identity SMILES and the deposited
geometry: how many atoms of each element carry which mass number, never
*which* atoms.

Consequence: an identity of `[2H]OC` (CH3-OD) **accepts** a geometry that
labels a methyl hydrogen instead (CH2D-OH). Both carry one deuterium, so the
check passes. These are different molecules with different zero-point
energies, and a consumer computing a partition function from the geometry gets
a different species from the one the identity names. This is a false
*acceptance*; the check never rejects a correctly-labelled deposit.

Not fixed because closing it needs an atom-level correspondence between the
SMILES graph and the XYZ atom ordering, which this repository does not have.
3D bond perception fails silently on exactly the strained and radical cases
where it would matter most, so inferring a mapping would replace a documented
gap with an undetectable one.

**Authority for masses.** The *geometry* is authoritative for per-atom masses
(`geometry.isotopes`, stored per atom index on `geometry_atom`) — that is what
a normal-mode analysis reads. The SMILES is authoritative only for *identity*:
it decides which `species_entry` a deposit resolves to, via `isotope_key`. A
consumer needing position-resolved isotope labelling must read the geometry,
never the SMILES.

### Typed evidence descriptors are asymmetric (NB8, deferred)

Transition-state reads carry an always-present typed descriptor
(`validation: {"irc": "present" | "absent" | "failed"}`) so a consumer never
has to infer validation status from a missing block. Kinetics reads do not
have the equivalent for tunneling or for interpretation assignments.

Consequence: a default kinetics read can show `tunneling_model: "eckart"`
alongside `tunneling_application: null`, and that is indistinguishable from
"the client did not request `include=interpretations`". The information is
recoverable — `include=interpretations` returns the real block — but the
default read is ambiguous in a way the TS surface deliberately is not, which
is asymmetric with the stated principle that evidence status should be
readable without an include token.

Deferred: closing it means adding always-present descriptors to the kinetics
read surface, which changes the default response shape for every kinetics
consumer. It belongs with the next read-API version, not with this stage.

### Interpretation completeness does not require Q‡ (NB9, warned not enforced)

`KineticsUploadRequest.validate_interpretation_content` requires a complete
reactant/product interpretation set once any assignment is offered, but
requires the `transition_state` subject only when tunneling evidence is
present.

Consequence: a canonical-TST rate can name every reactant and product
partition function and omit the transition state's, leaving the activated
complex unaccounted for while the deposit still looks fully interpreted.

Hard-requiring it is not correct: a rate whose parameterization comes from a
master-equation fit has no single dividing surface to point at. So the gap is
**reported rather than enforced** — `collect_kinetics_content_warnings` emits
`missing_kinetics_transition_state_interpretation` whenever an interpretation
set omits the transition state and the record does not delegate its
parameterization to a network fit via `network_kinetics_ref`. Variational TST
is intentionally *not* exempt: VTST still evaluates a partition function at
the variational dividing surface.
