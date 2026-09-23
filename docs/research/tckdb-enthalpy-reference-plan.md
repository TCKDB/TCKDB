# Enthalpy reference implementation

Owner decision: 2026-09-23. The owner revised the original scalar iff proposal to the two-layer rule below.

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
explicit adapter configuration; its output does not establish a basis. The
SDF adapter likewise requires explicit `enthalpy_reference_kind` configuration
(or `--enthalpy-reference-kind` on its CLI); H298 and software labels do not
establish a zero. Recorded test fixtures supply their convention explicitly.

## Reproduction before implementation

Base: origin/main 5988fbb890674eddb1819a164079612b0e79aff1, Alembic d2f4a7c1b8e6.
The model docstring asserted formation enthalpy; the model CHECKs, upload
validators and wire enums carried no declaration. The existing CCCBDB
builder tests passed (12 passed) while expecting H2 h298=0 and point h=8.468
at the same 298.15 K: formation energy and sensible increment on one record.
A scratch database probe stored an undeclared h298 scalar, approved it, and
verified that an UPDATE failed with `immutable` (1 passed).

## Verification plan

Pin each payload guard, all three upload routes, client construction, read
nulls, ARC refusal and CCCBDB routing. Exercise upgrade/downgrade with legacy
approved and unapproved rows. Remove each guard in turn and record the
named test failure, then restore it. Run the three gates sequentially with
worktree-local schemas and isolated tckdb_test databases, followed by the
schema, vocabulary, version, ASCII, client and parity checks. Run complete
GitNexus change analysis before each commit. No production access or data
migration is part of this work.
