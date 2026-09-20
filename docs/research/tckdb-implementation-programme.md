# TCKDB implementation programme

Companions: [Detailed phase A plan](tckdb-phase-a-implementation-plan.md), [Detailed phase B plan](tckdb-phase-b-implementation-plan.md), [Detailed phase C plan](tckdb-phase-c-implementation-plan.md). Implementation evidence: [Phase A](tckdb-phase-a-verification.md), [Phase B](tckdb-phase-b-verification.md), [Phase C](tckdb-phase-c-verification.md).

## Objective and publication boundary

Deliver a defensible first paper demonstrating chemical identity → computational evidence → thermo/kinetics products → attributed review → frozen release → reproducible downstream use.

The first-publication scope is the roadmap's **A/B molecular programme plus the Phase C interchange demonstration** (decided 2026-09-19): one independently generated QCSchema bundle and one ThermoML ideal-gas heat-capacity subset, each imported through a bounded, versioned adapter and compared with existing TCKDB evidence. This supersedes the earlier sentence that the adapters follow the paper; a manually curated, rights-cleared external reference comparison remains the ceiling of what the paper claims about external data.

Keep four categories distinct: confirmed defects (reproduced validation holes and field loss); implemented foundations (storage, provenance, curation, releases, archives and selected evaluators); proposed capabilities (general evaluation, correlated uncertainty, reconciliation, experimental adapters and additional domains); and unverified outcomes (corpus accuracy, production health, rights clearance and independent reproduction).

## Phases and gates

| Phase | Deliverables | Dependencies | Completion gate |
| --- | --- | --- | --- |
| A — trustworthy molecular contract | Preserve state and 0 K information; structural validation; honest export eligibility; claim-to-evidence boundaries. | Current implementation; restored impact tooling before edits. | Full-path regressions and legacy compatibility pass; unsupported claims removed or deferred. |
| B — first-publication evidence | Bounded corpus, rights, independent comparisons and disagreement, evidence replay, downstream use, deposited reproduction. | A for final ingestion/freeze; rights and corpus selection may begin alongside A. | Independent regeneration of every reported result from a rights-cleared deposit; manuscript matches it. **First paper may proceed here.** |
| C — experimental and calculation interchange | Observation, state, uncertainty-description and derivation contracts; bounded QCSchema, then narrow ThermoML. | A, B rights framework; experimental semantics before ThermoML normalization. | Versioned examples preserve identity, definitions, conditions, provenance and uncertainty meaning; unsupported mappings reported; idempotent ingestion. |
| D — molecular evaluation and consistency | Applicable species/reaction thermodynamics, molecular reconstruction, covariance propagation and thermo–kinetics checks. | A, explicit state/reference conventions; C lineage/uncertainty when experimental evidence is used. | Independent engine and analytical uncertainty tests; explicit handling of incompatible states, absent covariance and shared evidence. |
| E — assessed recommendations | Versioned constraints, reconciliation, residuals, influence and assessment covariance. | C/D and expert-approved uncertainty/independence assumptions. | Held-out comparisons against declared baselines demonstrate benefit, calibration and interpretable failures. Separate methods claim. |
| F — chemistry expansion and federation | Individual solution/biochemical, nonideal-fluid/mixture, solid/phase domains and authorized connectors. | Common contracts, domain experts and validated engines; E only for reconciliation claims. | Real end-to-end example per domain, state/model preservation, rights review, independent numerical validation. |
| G — active improvement | Rank and execute calculations/experiments using measured uncertainty reduction and cost. | Validated D/E assessments and reliable execution. | Prospective improvement over random and heuristic acquisition, counting failures and actual costs. |

Governance, contributor support, security, recovery and performance continue throughout.

## First-publication work package

1. Curate a corpus containing identity challenges, complete/incomplete chains, genuine independent disagreement, and supported thermo/kinetics cases. Report families, charge/spin/isotopes, methods, temperature/pressure domains, missing evidence and usable uncertainty.
2. Record contribution terms and source permissions, review historical deposits, and prevent release of unresolved selections. Accounts and software defaults do not imply redistribution consent.
3. Demonstrate identity preservation, evidence replay, numerical mechanism interoperability, reproducible review/selection and compatible external comparison. Hessian reanalysis requires frozen inputs, conventions and executable scripts.
4. Deposit software commit, migration head, pinned environments, release artifacts, required archives/raw evidence, citations, source versions, scripts and expected outputs, bound by digest. Separate post-processing replay from fresh electronic-structure computation.
5. Record external use and independent clean-environment reproduction, including failures and interventions.
6. Demonstrate paired database/artifact recovery and application/schema compatibility before claiming a durable hosted service. A backend paper need not promise production availability or require a new release frontend.
7. Generate manuscript numbers/figures from the deposit, use actual persistent identifiers, and reconcile comparison claims and citation metadata.

Historical counts, synthetic scale tests and passing CRUD suites do not substitute for these gates.

## Scientific engines

TCKDB owns custody, applicability, provenance, orchestration, comparison and release reproducibility. Use [Cantera](https://www.cantera.org/stable/reference/thermo/species-thermo.html) for independent NASA7/NASA9 and mechanism interpretation, retaining reference pressure. Use [RMG/Arkane](https://reactionmechanismgenerator.github.io/RMG-Py/reference/arkane/index.html) for supported statistical mechanics, reconstruction and pressure dependence, preserving versions and assumptions; use [RMG Wilhoit](https://reactionmechanismgenerator.github.io/RMG-Py/reference/thermo/wilhoit.html) as that representation's numerical reference. Pinned QCElemental validates supported QCSchema profiles; it does not calculate thermochemistry. Choose established solution/fluid/phase engines during F design subject to suitability and access terms. Do not build a new EOS or master-equation solver merely to integrate one. Do not present ATcT as an interchangeable backend solver or claim to reproduce its assessment method.

## Scientific hold points

| Decision | Required before | Work available meanwhile |
| --- | --- | --- |
| H/G reference basis, formation versus sensible quantities and elemental references | General Gibbs/Kirchhoff/Hess checks in D; normalized experimental comparisons in C | A structural validation and exact preservation |
| Experimental pilot property, state profile, uncertainty convention | ThermoML normalization | QCSchema pilot and documented B comparisons |
| Shared sources, systematic error, covariance, uncertainty coverage | D propagation and E reconciliation | Rights, provenance, candidate preservation |
| Corrections, rotor projection, conformer populations, electronic states and reconstruction approximations | Applicable B replay claims; general D reconstruction | A repair and inventory |
| Corpus membership, reference independence, accuracy targets by family | B benchmark and freeze | Contract fixtures and reproduction infrastructure |
| Activities, standard states, polymorph and phase-model conventions | Each F extension | Discovery and source attribution |

These hold points do not authorize inventing conventions or accuracy thresholds.
