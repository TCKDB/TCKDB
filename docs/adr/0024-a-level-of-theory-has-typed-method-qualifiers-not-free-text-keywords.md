# A level of theory has typed method qualifiers, not free-text keywords

**Status: accepted 2026-11-05** (owner decision). Related: [0021](0021-composite-levels-of-theory-are-a-recipe-bound-to-a-level-not-a-weighted-level.md)
(core treatment joins the hash only when stated; this record extends that pattern).

## Decision

`level_of_theory.keywords` is removed from the level's identity and dropped. Keywords are parameters of a calculation, "not
necessarily the level of theory itself". When a deck or log is uploaded they are read from it and stored as calculation
parameters, reconciled against what the depositor declared; they are not typed as free text on the level.

Four typed fields take over the qualifiers that free text was being used for. Each is a nullable column that joins `lot_hash`
**only when stated** (a NULL adds no key, and is never folded to a placeholder), the pattern ADR 0021 set for `core_treatment`:

| field | values |
|---|---|
| `relativistic_hamiltonian` | `none`, `dkh0`, `dkh2`, `dkhso`, `resc`, `x2c`, `zora`, `iora` |
| `pno_truncation` | `loose`, `normal`, `tight` |
| `ri_approximation` | `none`, `rij`, `rijcosx`, `rijk`, `rijonx` |
| `core_treatment` (existing) | `frozen_core`, `all_electron`, and the new `window` (a partial frozen core) |

Each value set is the closed set of the parsed parameter of the same meaning (`relativistic.hamiltonian`, `pno.truncation`,
`ri.approximation`, `core.frozen` in `app/services/parameter_closed_values.py`), so the typed field and the parameter share one
vocabulary. An explicit `none` is a stated value and a different level from not stating the field.

## What was left out, and why

- **No `keywords` on the wire, and no reroute.** A payload that sends it is refused like any unknown field. Nothing maps it into
  parameters, because that would be a silent guess about free text.
- **The specific frozen-core window is not identity.** `window` is one value: Gaussian counts orbitals, ORCA gives an energy
  window or an electron count, and no number is portable between them. The window itself stays on the calculation's
  `core.frozen` parameter rows, so two different windows are one level of theory.
- **Numeric DLPNO thresholds are not identity.** Only the three presets ORCA documents are. A hand-set `TCutPNO` stays a
  parameter row and the field stays NULL.
- **`dkh1` and arbitrary-order Molpro DKH are not in the set.** They stay NULL until a producer needs them named, and the
  shared closed set is widened in one place.

## Reconciliation

The warning `level_of_theory_disagrees_with_artifact` (already used for method and basis) also compares each stated qualifier
with the parameter of the same meaning read from the uploaded deck or log, with `context.component` naming the qualifier. It fires
only when both sides state a value and they differ, for the same program. A deck that is silent about a qualifier is never a
disagreement: no program default is inferred. The declared level is kept.

## Consequences

- Every stored `lot_hash` changes (the `"keywords"` key left the payload). The deployed instance is dev data and is reseeded,
  not re-keyed. The migration `d5f2b8c1a704` refuses to run while `level_of_theory` holds a row, and its downgrade refuses
  likewise. There is no merge or re-key machinery for existing rows.
- The historical re-key revisions are unchanged. Their tests compare against a frozen copy of the pre-decision hash that lives
  in the tests (`tests/_frozen_lot_hash.py`), not in application code.
- Level labels write `rel=`, `pno=` and `ri=` when stated, after `core=`.
- Level-of-theory browse and search filter on the three new fields beside `core_treatment`.
