# TCKDB implementation programme

Companions: [Detailed phase A plan](tckdb-phase-a-implementation-plan.md), [Detailed phase B plan](tckdb-phase-b-implementation-plan.md), [Detailed phase C plan](tckdb-phase-c-implementation-plan.md), [Detailed phase D plan](tckdb-phase-d-implementation-plan.md). Implementation evidence: [Phase A](tckdb-phase-a-verification.md), [Phase B](tckdb-phase-b-verification.md), [Phase C](tckdb-phase-c-verification.md), [Phase D runnable subset](tckdb-phase-d-verification.md).

Rewritten 2026-09-20. The earlier version of this document laid out seven
phases, A to G, as one continuous programme. Phases A to C are implemented
and deployed; phases D to G described work that would turn a database of
record into an evaluation engine, an assessment service, a platform for
every chemistry domain, and an experiment planner. That is more than one
project should promise, and most of it is other groups' research. This
version keeps the committed scope to what makes TCKDB defensible, names
the one follow-on that fits, and moves the rest to directions TCKDB would
host rather than build.

## Objective and publication boundary

Deliver a defensible first paper demonstrating chemical identity → computational evidence → thermo/kinetics products → attributed review → frozen release → reproducible downstream use.

The first-publication scope is the **A/B molecular programme plus the Phase C interchange demonstration** (decided 2026-09-19): one independently generated QCSchema bundle and one ThermoML ideal-gas heat-capacity subset, each imported through a bounded adapter and compared with existing TCKDB evidence. A manually curated, rights-cleared external reference comparison remains the ceiling of what the paper claims about external data.

Keep four categories distinct: confirmed defects (reproduced validation holes and field loss); implemented foundations (storage, provenance, curation, releases, archives and selected evaluators); proposed capabilities (anything in the directions section below); and unverified outcomes (corpus accuracy, production health, rights clearance and independent reproduction).

## What TCKDB is, and is not

TCKDB is a database of record for thermochemistry and kinetics evidence. It owns identity, custody, provenance, applicability, attributed curation, comparison, and release reproducibility. Every table is one of identity, provenance, result, or curation, and those roles stay separate.

TCKDB does not compute thermochemistry, assess recommended values, decide what to calculate next, or model every phase of matter. Where a number has to be evaluated, a pinned external engine does it and TCKDB records the engine, its version and its assumptions. Where a recommendation exists, TCKDB holds it with its inputs and provenance; it does not produce it.

## Committed scope

| Phase | Deliverables | Status | Completion gate |
| --- | --- | --- | --- |
| A — trustworthy molecular contract | Preserve state and 0 K information; structural validation; honest export eligibility; claim-to-evidence boundaries. | Done, deployed ([evidence](tckdb-phase-a-verification.md)). | Full-path regressions and legacy compatibility pass; unsupported claims removed or deferred. |
| B — first-publication evidence | Bounded corpus, rights, independent comparisons and disagreement, evidence replay, downstream use, deposited reproduction. | Code done and deployed ([evidence](tckdb-phase-b-verification.md)); corpus, deposit and manuscript numbers are freeze-time steps taken once, after development is declared finished. | Independent regeneration of every reported result from a rights-cleared deposit; manuscript matches it. **First paper proceeds here.** |
| C — experimental and calculation interchange | Observation, state and uncertainty-description contracts; bounded QCSchema import and export; ThermoML ideal-gas heat capacity, from the NIST archive or from any uploaded file. | Done, deployed ([evidence](tckdb-phase-c-verification.md)); the two paper demonstrations need author-run computations. | Examples preserve identity, definitions, conditions, provenance and uncertainty meaning; unsupported mappings reported; idempotent ingestion. |
| D — consistency checks | Advisory checks that a record agrees with itself and with its neighbours: Gibbs and Kirchhoff relations, Hess cycles, thermo against kinetics, computed against observed. Each is a review-tier check that writes findings and never changes a status or a selection, in the shape the Cp comparison already has. | Runnable subset implemented and verified locally ([evidence](tckdb-phase-d-verification.md)); not committed or deployed. Gibbs, Kirchhoff and Hess remain held. | Independent engine tests and analytical cases for every check; incompatible states and absent uncertainty handled explicitly, never guessed. Held behind the reference-basis hold point where a check needs one. |

Governance, contributor support, security, recovery and performance continue throughout.

The interchange scope grows as the schema grows: an importer maps what the
tables can hold today and reports the rest. Widening it is ordinary work,
not a phase.

## First-publication work package

1. Curate a corpus containing identity challenges, complete/incomplete chains, genuine independent disagreement, and supported thermo/kinetics cases. Report families, charge/spin/isotopes, methods, temperature/pressure domains, missing evidence and usable uncertainty.
2. Record contribution terms and source permissions, review historical deposits, and prevent release of unresolved selections. Accounts and software defaults do not imply redistribution consent.
3. Demonstrate identity preservation, evidence replay, numerical mechanism interoperability, reproducible review/selection and compatible external comparison. Hessian reanalysis requires frozen inputs, conventions and executable scripts.
4. Deposit software commit, migration head, pinned environments, release artifacts, required archives/raw evidence, citations, source versions, scripts and expected outputs, bound by digest. Separate post-processing replay from fresh electronic-structure computation.
5. Record external use and independent clean-environment reproduction, including failures and interventions.
6. Demonstrate paired database/artifact recovery and application/schema compatibility before claiming a durable hosted service. A backend paper need not promise production availability or require a new release frontend.
7. Generate manuscript numbers/figures from the deposit, use actual persistent identifiers, and reconcile comparison claims and citation metadata.

Historical counts, synthetic scale tests and passing CRUD suites do not substitute for these gates. Items 1, 4, 5 and 7 happen once, at freeze, after every code change the paper depends on has landed; they are not next steps while the database is still being developed.

## Scientific engines

TCKDB owns custody, applicability, provenance, orchestration, comparison and release reproducibility. Use [Cantera](https://www.cantera.org/stable/reference/thermo/species-thermo.html) for independent NASA7/NASA9 and mechanism interpretation, retaining reference pressure. Use [RMG/Arkane](https://reactionmechanismgenerator.github.io/RMG-Py/reference/arkane/index.html) for supported statistical mechanics, reconstruction and pressure dependence, preserving versions and assumptions; use [RMG Wilhoit](https://reactionmechanismgenerator.github.io/RMG-Py/reference/thermo/wilhoit.html) as that representation's numerical reference. Pinned QCElemental validates supported QCSchema documents; it does not calculate thermochemistry. Do not build a new EOS or master-equation solver merely to integrate one. Do not present ATcT as an interchangeable backend solver or claim to reproduce its assessment method. Engines are pinned external references: none of them shapes a table or an API.

## Directions TCKDB would host, not build

These were phases E, F and G. They stay out of the committed scope. Each
enters the programme only when a named user brings real data, a named
engine exists for the numbers, and the rights review is done; none enters
on a calendar or because the schema could take it.

- **Assessed recommendations** (was E: constraints, reconciliation, residuals, influence, assessment covariance). This is the research programme of groups such as Active Thermochemical Tables. TCKDB's part is custody: hold an assessment with its inputs, method version, independence assumptions and covariance, attributed to whoever produced it, and let a release cite it. TCKDB does not reconcile, and does not claim a methods contribution.
- **Other chemistry domains** (was F: solution and biochemical, nonideal fluid and mixture, solid and phase). Each needs a state and composition model, an established engine, a domain expert and a rights review before a single table is designed. The schema is extended first; an importer follows, never the reverse.
- **Active improvement** (was G: ranking and executing calculations or experiments by expected uncertainty reduction and cost). Deciding what to compute next belongs to the groups that run the calculations. TCKDB records what was run, why it was chosen if the producer says, and what it changed.
- **Federation and connectors.** Authorized read connectors to other databases are custody work like the CCCBDB and ThermoML importers: raw bytes by digest, source terms attested, unsupported content reported.

## Scientific hold points

| Decision | Required before | Work available meanwhile |
| --- | --- | --- |
| H/G reference basis, formation versus sensible quantities and elemental references | Gibbs, Kirchhoff and Hess checks in D; normalized enthalpy and Gibbs comparisons from experimental sources | Checks that need no reference basis (heat capacity, entropy, thermo against kinetics) |
| Shared sources, systematic error, covariance, uncertainty coverage | Any hosted assessment that claims independence | Rights, provenance, candidate preservation |
| Corrections, rotor projection, conformer populations, electronic states and reconstruction approximations | Applicable B replay claims | A repair and inventory |
| Corpus membership, reference independence, accuracy targets by family | B benchmark and freeze | Contract fixtures and reproduction infrastructure |
| Activities, standard states, polymorph and phase-model conventions | Any domain extension | Discovery and source attribution |

These hold points do not authorize inventing conventions or accuracy thresholds.
