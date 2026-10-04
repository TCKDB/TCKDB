# Network selection rules: what is registered and what is missing

Pressure-dependent network selection decides which candidates are *eligible* before it compares any protocol. A
preference between two eligible candidates needs a rule, and a rule needs evidence about the **network observable**
it speaks for; nothing transfers from H298 or from elementary rate rules.

**No rule is active in this release.** The registry holds five audited candidates, each registered inactive with its
missing prerequisites. They are listed in `backend/app/chemistry/network_rules/network_rule_candidates.yaml`, loaded
only against a pinned SHA-256, and a decision records which of them it consulted and why none was applied. Activation
is the owner's signed act in that file, *and* the rule's predicates must then be written and reviewed; neither exists.

| Rule | Objective | What it would say | Why it is inactive |
| --- | --- | --- | --- |
| `N-AMEDRO-HE-FC` | representation fidelity (named dataset reproduction) | OH + NO2 in He: a He-specific broadening factor reproduces the stated dataset better than the N2 value | the compared objects are Troe falloff fits, not network fits; the dataset points, fit parameters and uncertainties were not extracted; no verified evidence state exists |
| `N-JG-REDUCTION-FIDELITY` | model fidelity | one pinned PES, bath, cell set and window: a reduction closer to the energy-grained flux over a less close one | article not read in full (403); cells, metric and variant definitions unaudited; the study's reduction names are not mapped to TCKDB's vocabulary; a transient flux is not a phenomenological coefficient |
| `N-JM-CH4-TRANSFER` | physical accuracy | CH4 baths: one interaction-potential transfer treatment over another against the study's reference data | abstract-level evidence only; a protocol declaration cannot name the transfer treatment, so a candidate cannot state what the rule compares |
| `N-ME-CONVERGENCE` | model fidelity | a verified converged solve over a demonstrated inadequate one of the same model | a proposed rule with no source comparison; no verification step; "same model" is not a stated fact |
| `N-REP-HELDOUT` | representation fidelity | the fit with the better held-out error under one declared metric | no pinned held-out set; no verification step |

## What would be needed (for the owner)

Nothing was downloaded from a paywalled source for this audit. To consider activating any of these, the owner would
have to supply or authorise:

- **Amedro et al., Atmos. Chem. Phys. 20, 3091 (2020)** (open access): the table of experimental conditions and
  measured rate coefficients, the table of fitted k0, kinf and Fc per bath, the uncertainty statement, and the dataset
  in machine-readable form; and a decision on whether a Troe-falloff comparison belongs to network selection at all.
- **Johnson and Green, Faraday Discuss. (2022), d2fd00040g** (RSC): the full text, the definition of each reduction
  variant, the PES variants, bath, every temperature and pressure cell, initial conditions, observation time, the
  error metric and per-cell results, plus the electronic supplementary information; and a mapping of the study's
  reduction names to TCKDB's `reduction_method` vocabulary.
- **Jasper and Miller, J. Phys. Chem. A (2011), jp200048n** (ACS): the full text and supporting information (the
  interaction potentials, baths, temperature range, tabulated transfer averages and uncertainties); and a typed
  energy-transfer treatment field in the protocol declaration.
- For convergence and held-out rules: the criterion, a pinned reference and its uncertainty, and the verification
  procedure that would turn a producer's declared validation entry into a verified one.

Evidence a producer states about its own solve is stored as *declared*. It never satisfies a prerequisite that needs
verified evidence.
