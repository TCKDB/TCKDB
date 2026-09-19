# TCKDB scientific coverage and publication audit

Audit date: 2026-09-18. Source checkout: `8539c927d6ca81758d28a67c0a0fbac31a13c9af`, with current working-tree files inspected. This audit addresses the scientific requirements in [the landscape report](deep-research-report.md), especially its distinction between observations, calculations, evaluated results, and fitted models. It does not independently re-verify that report's external database claims.

**Assessment:** TCKDB already has a substantial, unusually detailed molecular computational-chemistry backbone. Its strongest scientific opportunity is connecting that backbone to experimental evidence, explicit state conventions, correlated uncertainty, and reproducible evaluation. The inspected implementation does not yet justify a claim of universal thermochemical coverage or ATcT-like reconciliation. Several narrower scientific API and validation gaps should be resolved before a paper presents the current system as scientifically complete.

## Scope and evidence standard

- **Implemented** means a concrete current source model/service/schema exists; it does not mean a production population or successful external benchmark was verified.
- **Partial** means useful capability exists but does not meet the complete scientific requirement.
- **Not found** means no implementation was located in the inspected model, workflow, schema, chemistry, and service surfaces, including targeted searches; it is not proof that no external script or unexamined branch implements it.
- Vtrace `get_code_context` supplied initial orientation. GitNexus MCP queries failed because its index storage version was 43 while the running engine expected 42. The coordinating agent handled index refresh. Findings below were verified in current source files rather than inferred from the graph. No application code was changed, no deployment inspected, and no full suite or persisted-upload tests run. The schema probes below ran successfully in `tckdb_env`.

Source links point to files; accompanying line numbers identify the inspected evidence and may drift after edits.

## What TCKDB already does well

| Capability | Status and evidence | Scientific value and boundary |
|---|---|---|
| Molecular identity beyond InChIKey | **Implemented.** [species.py](../../backend/app/db/models/species.py), lines 43–82, uses canonical SMILES, charge, and multiplicity for species identity; lines 100–163 distinguish resolved electronic/isotopic forms and atom-resolved isotope identity. | Avoids collapsing tautomers that share standard InChI identity, and distinguishes spin states. This is molecular identity, not a crystal-polymorph or solution-state model. |
| Separate conformer observations and choices | **Implemented.** [species.py](../../backend/app/db/models/species.py), classes `ConformerGroup` at line 218, `ConformerObservation` at 264, `ConformerSelection` at 316, and `ConformerAssignmentScheme` at 368. | A geometry observation and a scientific choice of conformer are distinct objects. This is a strong basis for reproducible ensemble treatments. |
| Thermochemistry models | **Implemented.** [thermo.py](../../backend/app/db/models/thermo.py), lines 109–143 and 217–395, stores formation enthalpies, entropy, tabulated Cp/H/S/G, NASA-7, NASA-9 intervals, and Wilhoit. [common.py](../../backend/app/db/models/common.py), lines 677–689, explicitly classifies representation. | Supports important molecular/combustion use cases. Representation storage does not establish numerical consistency, fit quality, or a general EOS/phase-model capability. |
| Detailed statistical-mechanics provenance | **Implemented.** [statmech.py](../../backend/app/db/models/statmech.py), lines 58–130, allows species or transition-state ownership, symmetry, rotor classification, frequency scaling, and optical-isomer treatment; later classes store electronic levels, source calculations, and torsions. | Preserves physical assumptions that are commonly lost in a final Cp or rate fit. The schema does not itself prove an independent end-to-end recomputation. |
| Energy corrections as reusable scientific objects | **Implemented.** [energy_correction.py](../../backend/app/db/models/energy_correction.py), `EnergyCorrectionScheme` at line 44 and its software/workflow release references at lines 71–98; [thermo.py](../../backend/app/db/models/thermo.py), lines 185–191, links group-additivity breakdowns. | Supports method/correction traceability instead of attaching an unexplained corrected number. |
| Rich molecular kinetics | **Implemented.** [common.py](../../backend/app/db/models/common.py), lines 709–728, enumerates Arrhenius, modified/multi-Arrhenius, Lindemann, Troe, SRI, PLOG, and Chebyshev. [kinetics.py](../../backend/app/db/models/kinetics.py), lines 73–159 and 265 onward, records direction, third bodies, network links, degeneracy conventions, pressure, interpretation assignments, and tunneling applications. | A strong foundation for mechanistic kinetics and combustion exchange. It is not yet a general archive of raw time-series experiments and experimental apparatus/calibration. |
| Pressure-dependent reaction-network provenance | **Implemented.** [network_pdep.py](../../backend/app/db/models/network_pdep.py), `NetworkSolve` at line 312, separates computed, reported, and fitted evidence; state energies, bath gas, energy transfer, and rate fits have dedicated objects. [network_kinetics_eval.py](../../backend/app/chemistry/network_kinetics_eval.py), lines 254–495, evaluates Chebyshev/PLOG with explicit range indicators. | Pressure-dependent kinetics is substantive existing capability. This network is not an ATcT-style thermochemical evidence-reconciliation network. |
| Conservation checks | **Implemented.** [reaction_resolution.py](../../backend/app/services/reaction_resolution.py), lines 1009–1017, invokes elemental and charge conservation before reaction resolution; [test_reaction_charge_conservation.py](../../backend/tests/workflows/test_reaction_charge_conservation.py) and [test_structure_invariants.py](../../backend/tests/invariants/test_structure_invariants.py) exercise relevant workflows. | Runtime scientific safeguards exist. These should be clearly separated from thermodynamic cycle closure, which was not found. Tests were inspected, not rerun. |
| Preservation of unresolved source observations | **Implemented, narrower scope.** [molecular_property_observation.py](../../backend/app/db/models/molecular_property_observation.py), lines 10–35 and 151–184, preserves unresolved identity, external record/release identifiers, source digests, parser version, and raw payloads. | Avoids inventing identities when imported formula/name evidence is ambiguous. This is a useful pattern to extend to thermochemical measurements. |
| Scientific versioning foundations | **Implemented.** [dataset_release.py](../../backend/app/db/models/dataset_release.py), lines 118–154 and 372 onward, models versioned curation policies and release manifests; [scientific_record_supersession.py](../../backend/app/db/models/scientific_record_supersession.py), line 19, provides replacement relationships. | Good infrastructure for frozen, citable scientific selections. Review/release selection is not equivalent to quantitative critical evaluation or uncertainty reconciliation. |

## Gaps that prevent a universal scientific offering

### 1. State and reference conventions are present but incomplete and inconsistently exposed

The thermo model correctly distinguishes standard formation enthalpy at 0 K and 298.15 K, standard entropy, reference pressure, and physical phase ([thermo.py](../../backend/app/db/models/thermo.py), lines 39–63). However, phase and reference pressure remain nullable. The four phase tokens are gas, liquid, solid, and aqueous ([common.py](../../backend/app/db/models/common.py), lines 165–176).

No typed thermodynamic state object was found for solvent composition, concentration/activity convention, ionic strength, pH/transformed biochemical standard state, crystal polymorph, or phase transition. `LevelOfTheory.solvent` and `solvent_model` are computational-method metadata, not substitutes for these state variables. Likewise, a `solid` token cannot distinguish graphite from diamond; an `aqueous` token cannot define an aqueous-ion reference convention.

There is also an immediate read-contract gap: [scientific_thermo.py](../../backend/app/schemas/reads/scientific_thermo.py), lines 250–280, and [scientific_read/thermo.py](../../backend/app/services/scientific_read/thermo.py), lines 463 onward, do not expose `phase`, `reference_pressure_bar`, or the 0 K formation-enthalpy fields on the ordinary scientific `ThermoRecord`. The analytics path exposes the 0 K values ([analytics.py](../../backend/app/services/scientific_read/analytics.py), lines 828–858). The finding is therefore **inconsistent surfacing on the ordinary scientific read**, not absence from every API.

Computed thermo uploads default omitted phase/pressure to gas/1 bar ([thermo_upload.py](../../backend/app/schemas/workflows/thermo_upload.py), lines 242–260). That is an explicit existing molecular-domain policy. A wider platform should restrict defaults to a declared domain profile and distinguish reported conventions from assumed ones.

**Acceptance:** every public scientific read, search result, export, and release retains the state/reference convention used to interpret its numbers; unknown state stays explicitly unknown. A benchmark must keep gas/liquid water, distinct solid polymorphs, 1 bar/1 atm conventions, and chemically distinct ionic solution states separate. Computed defaults must never silently reclassify a future condensed-phase import.

### 2. Observation, evaluation, and fit lineage is only partially separated

Calculations, statmech interpretations, thermo records, fitted subrecords, and source-calculation links are distinct. Experimental/computed/estimated origin is explicit. However, `ScientificOriginKind` has only those three origin values ([common.py](../../backend/app/db/models/common.py), lines 159–162): origin does not encode whether a datum is an original measurement, a critically evaluated recommendation, or a fit to several sources.

`ThermoPoint` has only parent identity, temperature, and four values ([thermo.py](../../backend/app/db/models/thermo.py), lines 217–236). It has no per-point measurement uncertainty, apparatus, calibration, sample composition/purity, or measurement-source relationship. Points can accompany a fit, but co-location is not a derivation graph. The thermo parent has a literature reference and links to calculations, not a general many-to-many evidence relationship spanning observations, normalization, evaluations, and fits.

`MolecularPropertyObservation` is a promising partial foundation; it also illustrates the next issue: `scalar_unit` is free text and state is largely `state_label_raw` ([molecular_property_observation.py](../../backend/app/db/models/molecular_property_observation.py), lines 103–116). Preserve original text, but introduce a typed normalized quantity/unit/state alongside it before allowing cross-source numerical comparisons. This is a scientific interoperability concern, not a recommendation to discard forensic source payloads.

**Acceptance:** one published demonstration follows original measurements from two sources through explicit normalization, a versioned evaluation, and a NASA fit. It preserves conflicting values, distinguishes copies of the same experiment, records fitting inputs/weights/software, and can invalidate or recompute descendants after an upstream correction.

### 3. Scalar uncertainties are useful; correlated uncertainty is not yet first-class

Thermo has scalar uncertainties for H298, S298, and formation enthalpy at 0 K ([thermo.py](../../backend/app/db/models/thermo.py), lines 112–120). Kinetics has A/n/Ea uncertainty and an additive-versus-multiplicative A convention ([kinetics.py](../../backend/app/db/models/kinetics.py), lines 130–136; [common.py](../../backend/app/db/models/common.py), lines 808–819).

No typed covariance matrix, cross-record correlation, confidence/coverage convention, separation of measurement/model/numerical uncertainty, or uncertainty-propagation service was found in the inspected application/schema surfaces. A multiplicative factor is not a specified confidence level. Separate coefficient uncertainties cannot describe correlated fitted parameters. NASA coefficients and tabulated values lack equivalent uncertainty structures.

**Acceptance:** uncertainty objects specify interpretation, confidence/coverage, quantity ordering, units, source, and covariance relationships; ingestion validates dimensions and positive-semidefiniteness within a documented tolerance. A reproducible example propagates correlated species enthalpies into reaction uncertainty, and correlated Arrhenius parameters into k(T), including a case where assuming independence produces a demonstrably different answer. Unknown uncertainty must remain distinct from zero.

### 4. Scientific consistency needs runtime services, not only reference-fixture tests

Current conservation checks are real. In contrast, [test_thermo_invariants.py](../../backend/tests/invariants/test_thermo_invariants.py), lines 108–174, computes a test-local Gibbs residual on deliberately consistent triples; lines 182–212 evaluate a fixed water NASA coefficient fixture using test-local functions. These tests do not demonstrate runtime detection of inconsistent user data.

The shared thermo schemas validate temperature positivity and some bound ordering, but NASA-7 coefficients are optional and `ThermoPoint` allows every property value to be absent ([tckdb_schemas/thermo.py](../../schemas/python/tckdb-schemas/tckdb_schemas/thermo.py), lines 23–40 and 49–104). The workflow's content check counts an empty NASA object or a temperature-only point as content; NASA-9 validation checks contiguous interval *indices* without checking cross-interval temperature overlap ([thermo_upload.py](../../backend/app/schemas/workflows/thermo_upload.py), lines 399–463). Read-only probes confirmed these acceptance cases; see appendix.

No application implementation was found for Hess-cycle closure, Cp-integrated H/S consistency, elemental reference-state closure, phase-transition closure, or forward/reverse kinetics versus equilibrium consistency. These are separate requirements from element/charge conservation.

**Acceptance:** versioned scientific checks run on actual submitted records and write machine-readable outcomes with residual, tolerance, inputs, and applicability. Tests must exercise those production paths with deliberately inconsistent records. Fit completeness, overlapping temperature regions, and empty tabulations should fail structural validation; unusual but legitimate evidence should be retained with reviewable warnings where appropriate. Thermodynamic relations must use matching conventions: in particular, do not combine formation enthalpy with absolute species entropy and call the result formation Gibbs energy without elemental-reference terms.

### 5. Molecular/combustion depth is not yet cross-domain breadth

The supported thermo models are NASA7, NASA9, Wilhoit, tabulated, and scalar; Shomate is absent from the enum. No native model/service was found for composition-dependent excess Gibbs energy, CALPHAD sublattices, fluid EOS, phase equilibria, transition latent heats, crystal/lattice thermodynamics, or aqueous/biochemical activity conventions. Transport stores Lennard-Jones parameters, dipole/polarizability, and rotational relaxation ([transport.py](../../backend/app/db/models/transport.py), lines 61–70); that is not a comprehensive temperature/composition-dependent viscosity, conductivity, or diffusion archive.

**Acceptance:** use domain extensions behind a shared evidence/state/uncertainty core. Prove each extension with a small, externally validated dataset and executable round trip before claiming support. Appropriate milestones are Shomate/reference molecular data; experimental pure-fluid/mixture properties; aqueous standard-state chemistry; and crystal/phase-model federation. The molecular provenance schema should not be forced to impersonate these domains by putting scientific content in `note` or generic JSON.

## Recommended sequence and paper claims

| Priority | Concrete deliverable | Completion evidence |
|---|---|---|
| P0: before scientific completeness claims | Repair ordinary thermo read state/0 K omissions; accept valid 0 K-only thermo; reject empty representations and ambiguous overlapping interval sets; clarify point reference conventions. | Contract tests spanning upload, read, export, and frozen release; adversarial examples have documented outcomes. |
| P0: before publication results | Establish a bounded molecular benchmark with external numerical references and fully replayable provenance. | Independent reconstruction from source artifacts, explicit errors/tolerances and failures, and a frozen data/code release. A populated schema or green CRUD suite alone is insufficient. |
| P1: main scientific differentiation | Typed observation/evaluation/fit derivation; correlated uncertainty; production consistency checks. | A measured cross-source reconciliation case with auditable conflicts, propagated uncertainty, and revised results after corrected input. |
| P1: trustworthy recommendations | Separate provenance completeness, reproducibility, scientific validation, and predictive/experimental accuracy in selection policies. | A record with excellent provenance but incorrect numerical content is not automatically recommended. Current thermo evidence completeness counts availability predicates ([scientific_read/thermo.py](../../backend/app/services/scientific_read/thermo.py), lines 1341–1423), so it must not be reported as a calibrated accuracy score. |
| P2: breadth | Add state-complete domain profiles and narrowly validated connectors/models. | Each claimed domain has a maintained contract, real reference cases, license/provenance preservation, and round-trip tests. |
| P3: frontier | Experimental/computational value-of-information and active-learning prioritization. | Prospective held-out study demonstrates uncertainty reduction or improved downstream predictions, accounting for shared-source leakage. |

A defensible first paper could establish **an evidence-preserving molecular thermochemistry and kinetics infrastructure joining chemical identity, calculation artifacts, statistical-mechanical interpretation, and citable scientific selections**. Its scientific results should be measured, not inferred from architecture. A subsequent reconciliation paper can claim more once evaluated recommendations and covariance-aware consistency exist. A universal-database claim would require the multidomain demonstrations above as well as governance, interoperability, and operational evidence assessed elsewhere.

## Appendix: read-only schema probes

Executed from `backend/` in `tckdb_env`. These instantiate Pydantic models only: no database writes, HTTP requests, approval/review processing, or commit-time constraints were exercised. Therefore **ACCEPTED means schema acceptance, not verified persistence or inclusion in a curated release**. This deliberately avoids presenting a schema hole as an observed production corruption.

Runnable from the repository root:

```bash
cd backend
conda run -n tckdb_env python -c '
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from pydantic import ValidationError

base = {
    "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
    "scientific_origin": "experimental",
}
cases = {
    "zero_k_only": {"enthalpy_formation_0k_kj_mol": -238.9},
    "empty_nasa": {"nasa": {}},
    "empty_point": {"points": [{"temperature_k": 298.15}]},
    "inconsistent_gibbs": {"points": [{
        "temperature_k": 298.15, "h_kj_mol": 10,
        "s_j_mol_k": 100, "g_kj_mol": 900,
    }]},
    "overlap_nasa9": {"nasa9_intervals": [
        {"interval_index": i, "t_min_k": 200, "t_max_k": 1000,
         **{f"a{j}": 0.0 for j in range(1, 10)}}
        for i in [1, 2]
    ]},
}
for name, values in cases.items():
    try:
        ThermoUploadRequest.model_validate({**base, **values})
        print(name, "ACCEPTED")
    except ValidationError as exc:
        print(name, "REJECTED", exc.errors()[0]["msg"])
'
```

Observed output:

```text
zero_k_only REJECTED Value error, Thermo upload must include at least one of: a scalar thermo value (h298_kj_mol or s298_j_mol_k), a NASA-7 block, a NASA-9 interval set, a Wilhoit block, or one or more thermo points.
empty_nasa ACCEPTED
empty_point ACCEPTED
inconsistent_gibbs ACCEPTED
overlap_nasa9 ACCEPTED
```

The Gibbs probe demonstrates absence of a schema-level relation check. Because point reference conventions themselves need clarification, it should motivate explicit semantics and applicability-aware validation, not an unconditional rule rejecting every externally reported formation-property triple.
