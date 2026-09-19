# First-publication claim-to-evidence checklist

Scope: [A/B molecular programme](tckdb-implementation-programme.md). Sources: [manuscript skeleton](../../paper/19__TCKDB_skeleton/README.md), [scientific audit](tckdb-scientific-coverage-audit.md), [platform audit](tckdb-platform-readiness-audit.md), and [publication audit](tckdb-landscape-publication-audit.md). This checklist is a release gate, not evidence that the experiments have been completed.

| Skeleton claim | Permitted scope | Evidence required before publication | Status |
| --- | --- | --- | --- |
| Abstract: other resources keep only a number and citation | Compare named, dated, directly inspected capabilities; omit categorical competitor claims | Primary-source comparison matrix with precise definitions | Unverified; categorical wording withdrawn |
| Abstract/results: MIT code, CC BY 4.0 data | Software license and deposit-specific redistribution rights are separate | Contribution terms, historical permission review, rights-cleared release selection | Rights gate in B |
| Abstract/conclusions: full traceability and re-computation | Representation can link available evidence; replay is demonstrated only for deposited cases | Complete selected chains, raw input/artifact hashes, engine/convention versions, executable replay | Foundations implemented; general reproducibility unverified |
| Conclusions: cross-source identity | Demonstrated identity cases for declared charge/spin/isotope/stereo/conformer scope | Frozen positive/negative fixtures and independently reviewed corpus cases | B benchmark required |
| Results 3.2: CHEMKIN round-trip | Complete gas NASA7 at 1.01325 bar and supported kinetic forms | Strict Cantera load plus independently evaluated properties/rates, printed precision bounds and expected gaps | A contract and B deposited experiment required |
| Results 3.3: lossless export | Recovery archive within its documented exclusions; selected NDJSON is a projection | Restore into empty DB; exact row/public-reference/float/artifact checks; paired recovery demonstration for service claims | Existing archive foundation; freeze exact evidence in B |
| Results 3.4: seven independent depositions | Distinct submissions establish separate deposit events, not independent scientific evidence | Source/calculation lineage and shared-data analysis; genuine independent disagreement example | Independence claim withdrawn pending B evidence |
| Results 3.4: evidence completeness and review | Availability predicates and attributed human decisions, not calibrated accuracy | Reproducible policy selection and review history on frozen cases | Implementation foundation; accuracy excluded |
| Results 3.5: Hessian reanalysis | Reconstruction from supplied primitives under declared masses, units and projection conventions | Frozen Hessians/geometries, scripts, parsed-frequency comparator, residuals and failures | Reported historical numbers not accepted as fresh evidence |
| Results/limitations: corpus counts and tool counts | Counts generated from the deposited snapshot and software revision | Digest-bound scripts and outputs | B required; historical live counts illustrative only |
| Limitations: scientific scope | Molecular evidence custody and selected evaluators; no general thermochemical evaluation/reconciliation claim | Explicit supported representation/applicability table | C–G remain proposed |
| Limitations: all gaps need no schema redesign | Remove this assertion; state, covariance and new domains require design | Reviewed future schema proposals and domain validation | Unsupported architectural prediction withdrawn |
| Conclusions: hosted, durable community service | Self-hosting support does not establish availability, recovery or external usefulness | Paired restore drill, compatibility check, external workflow and interventions | Unverified |
| Conclusions: first citable release enables a later paper | First paper may include a rights-cleared release once A/B gates pass | Persistent identifier, immutable bytes/digests, independent clean reproduction | B gate |

Representation, successful execution, reproducibility and accuracy are distinct claims. Tests of structure do not establish chemical accuracy. Replaying post-processing does not establish fresh electronic-structure reproducibility. Repeated ingestion of one source does not establish independent agreement.

Exclude universal-database coverage, ATcT-equivalent reconciliation, general Gibbs/Hess consistency, covariance propagation, universal reconstruction, and unsupported competitor comparisons from the first paper. Record unresolved H/G reference conventions, uncertainty meaning, shared evidence and physical approximations explicitly. Phase A completion alone is not submission readiness.
