# Network selection rules: what is registered and what is missing

Pressure-dependent network selection decides which candidates are *eligible* before it compares any protocol. A
preference between two eligible candidates needs a rule, and a rule needs evidence about the **network observable**
it speaks for; nothing transfers from H298 or from elementary rate rules.

**No rule is active in this release.** The registry holds six audited candidates, each registered inactive with its
missing prerequisites. They are listed in `backend/app/chemistry/network_rules/network_rule_candidates.yaml`, loaded
only against a pinned SHA-256, and a decision records which of them it consulted and why none was applied. Activation
is the owner's signed act in that file (a named person and a date), *and* the rule's predicates must then be written
and reviewed, rule by rule; neither exists. The registry is built when the API starts, so a bad pin fails the deploy.

| Rule | Level | Objective | What it would say | Why it is inactive |
| --- | --- | --- | --- | --- |
| `N-AMEDRO-HE-FC` | solves | representation fidelity (named dataset reproduction) | OH + NO2 in He: the He-specific broadening factor (Fc = 0.32) reproduces the stated dataset better than the N2 value (0.39) | the compared objects are Troe falloff fits, not network fits; Table 1 is located but not extracted; only the two He fits were audited; no verified evidence state exists |
| `N-JG-REDUCTION-FIDELITY` | solves | model fidelity | one pinned PES, bath, cell set and window: a reduction closer to the energy-grained flux over a less close one (council-reported, unverified) | the open-access article has not been read here; cells, metric and variant definitions unaudited; whether to add `simulation_least_squares` to the reduction vocabulary is open; a transient flux is not a phenomenological coefficient |
| `N-JM-CH4-TRANSFER` | solves | model fidelity | CH4 in He, Ne or H2: one interaction-potential transfer treatment over another against full-dimensional direct dynamics | abstract-level evidence only; `network_solve_energy_transfer.model` is free text, with no typed field for how the parameters were obtained |
| `N-JM-CH4-RATE` | solves | physical accuracy | CH4: the solve whose rates agree better with the study's experiments | the experimental comparison is in the paywalled full text; no verification step |
| `N-ME-CONVERGENCE` | solves | model fidelity | a verified converged solve over a demonstrated inadequate one of the same model | a proposed rule with no source comparison; no verification step; "same model" is not a stated fact |
| `N-REP-HELDOUT` | fits of one solve | representation fidelity | the fit with the better held-out error under one declared metric | no pinned held-out set; no verification step |

`N-REP-HELDOUT` is registered at the **representation level**: it can only compare alternate fits of one solve, so it
can never rank solves. The Jasper and Miller paper is registered as two rules because its trajectory tests against
direct dynamics are a model-fidelity claim, and only its final CH4 rates against experiment are physical accuracy.

## Sources the audit read

- **Amedro et al., Atmos. Chem. Phys. 20, 3091-3105 (2020)**, CC BY 4.0. The article PDF and the supplement (figures
  only) are pinned in the manifest by SHA-256. Anchors: the best He fit Fc = 0.32 with k0He = 1.4e-30 (p. 3095,
  section 3.1; Fig. 1 caption, p. 3094; Fig. S2), and the Fc = 0.39 comparison (about 5 % high above about 300 Torr,
  about 10 % low below). Table 1 (p. 3092) is the dataset, with 2-sigma uncertainties; it is open and not yet
  extracted.
- **Johnson and Green, Faraday Discuss. 238, 380-404 (2022)**, CC BY 3.0, open copies at MIT DSpace (hdl 1721.1/143677)
  and OSTI. Not read here; statements about it are council-reported and unverified.
- **Jasper and Miller, J. Phys. Chem. A 115, 6438-6455 (2011)**: the open abstract only.

## What would be needed (for the owner)

- **Amedro:** nothing more is needed from the owner to read it: it is open. Extract Table 1 (14 He points in all: 1 at
  277 K, 10 at 292 K and 3 at 332 K) with page and table anchors if the rule is to be pursued, and decide whether a Troe-falloff
  comparison belongs to network selection at all.
- **Johnson and Green:** open access. A browser fetch is needed only if a reduction-fidelity rule is wanted; then the
  reduction definitions, PES variants, cells, metric and results, and a decision on `simulation_least_squares`
  (the other reduction names already exist in `NetworkReductionMethod`).
- **Jasper and Miller (ACS, paywalled):** the full text and supporting information (the interaction potentials,
  temperature range, transfer averages against direct dynamics, and the experimental CH4 rates); and a typed
  energy-transfer treatment field in the protocol declaration.
- For convergence and held-out rules: the criterion, a pinned reference and its uncertainty, and the verification
  procedure that would turn a producer's declared validation entry into a verified one.

Evidence a producer states about its own solve is stored as *declared*. It never satisfies a prerequisite that needs
verified evidence.
