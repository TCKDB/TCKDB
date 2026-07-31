# TCKDB external landscape research

Date: 2026-07-30

Scope: primary/official sources relevant to the comparison and novelty claims in `paper/18__TCKDB/1_intro.tex`

## Bottom line

The manuscript has a plausible and useful product-level distinction: TCKDB is aimed specifically at
community-contributed, reviewable **gas-phase thermochemistry and kinetics products** while preserving links to
calculation evidence. The external evidence reviewed here does **not**, however, support the universal claim that
“only TCKDB combines all eight” capabilities in the current comparison table.

Several comparison systems provide more than the manuscript presently credits:

- AiiDA stores an immutable provenance graph, immutable node files, and re-importable archives that include the
  provenance of selected results.
- NOMAD accepts programmatic uploads, exposes raw and normalized archives through APIs, makes published uploads
  immutable, and has a documented self-hosted deployment.
- QCArchive is an open, self-hostable calculation service with a JSON web API, user-created datasets, server-side
  computation submission, and portable offline dataset views.
- ioChem-BD is an installable, federated computational-chemistry repository with upload, curation, publication,
  search, file retrieval, and REST APIs.
- NIST Chemical Kinetics stores many independent records per reaction and structured uncertainty, validity ranges,
  data type, experimental procedure, and bibliography fields.
- ATcT exposes a network-derived provenance analysis, influential determinations, correlated species, and
  uncertainties, rather than merely a preferred value plus citation.

The defensible novelty position is therefore narrower: **among the explicitly evaluated resources, TCKDB is
designed to integrate calculation-linked thermochemistry/kinetics products with distinct evidence-completeness,
human-review, and representative-selection concepts.** That is a design-intent claim, not yet proof that no
existing platform or extension has the same combination. It should be presented as a scoped comparison with
definitions, dated evidence, and implementation validation.

## Findings by resource

### AiiDA and Materials Cloud

AiiDA is substantially understated by the current table:

- AiiDA's official sharing guide says an export archive automatically includes the complete provenance of selected
  result nodes under its default traversal rules, can export groups or a whole profile, and can be imported into
  another AiiDA database. Materials Cloud can expose an uploaded AiiDA provenance graph interactively
  ([AiiDA: sharing data](https://aiida.readthedocs.io/projects/aiida-core/en/stable/howto/share_data.html)).
- Stored AiiDA node attributes are immutable, and sealed process nodes cannot receive new links
  ([AiiDA: process sealing](https://aiida.readthedocs.io/projects/aiida-core/en/stable/topics/processes/concepts.html)).
  Repository contents are also immutable after node storage
  ([AiiDA: repository internals](https://aiida.readthedocs.io/projects/aiida-core/en/stable/internals/storage/repository.html)).
- The archive is a read-only storage backend combining an SQLite database and file-object repository
  ([AiiDA: storage](https://aiida.readthedocs.io/projects/aiida-core/en/stable/topics/storage.html)).
- Materials Cloud accepts `.aiida` archives and can create an interactive analysis environment from a published
  record ([Materials Cloud Archive applications](https://archive.materialscloud.org/help/apps)).

Recommended correction: rate AiiDA as designed for queryable provenance, immutable stored records, file-backed
provenance, re-importable archives, and self-hosted/API access. Whether it represents multiple *thermochemical or
kinetic product values under one normalized chemical identity* should remain “not demonstrated for the core
platform,” not be inferred from its ability to hold multiple calculation nodes.

### NOMAD

NOMAD also challenges several TCKDB novelty columns:

- NOMAD has endpoints for creating and editing uploads, uploading raw files, retrieving full normalized archives,
  publishing, assigning DOIs, and transferring bundles between deployments
  ([NOMAD API](https://nomad-lab.eu/prod/v1/api/v1/extensions/redoc)).
- Both raw files and processed archive data can be downloaded per upload, per entry, or by query
  ([NOMAD download documentation](https://nomad-lab.eu/prod/v1/docs/howto/manage/program/download.html)).
- An upload is collaborative and mutable before publication; publication makes it immutable and publicly visible
  ([NOMAD glossary](https://nomad-lab.eu/prod/v1/docs/reference/glossary.html)).
- A self-hosted NOMAD instance is a documented “NOMAD Oasis”; the official distribution supports installations
  from a laptop through a production Kubernetes deployment
  ([NOMAD Oasis installation](https://nomad-lab.eu/prod/v1/docs/howto/oasis/install.html)).
- The official tutorial covers authenticated programmatic upload, metadata editing, collaboration, and publication
  ([NOMAD upload/publish API tutorial](https://nomad-lab.eu/prod/v1/docs/tutorial/upload_publish_api.html)).

Recommended correction: retain positive ratings for provenance, published-record immutability, community upload,
API, and self-hosting. Raw-artifact retrieval is a designed capability, not merely partial. “Recomputable” needs a
strict definition: NOMAD clearly preserves raw and normalized data, but preservation alone does not prove a
bitwise or scientifically equivalent rerun in a captured execution environment.

### QCArchive

QCArchive is more than a passive calculation archive:

- Its stated purpose is to run and store thousands to millions of quantum-chemistry computations for sharing,
  retrieval, analysis, or export. A server is controlled through a Python client and a language-independent JSON
  web API ([QCArchive overview](https://docs.qcarchive.molssi.org/overview/index.html)).
- Official instructions describe installing and operating one's own PostgreSQL-backed QCFractal server
  ([QCFractal installation](https://docs.qcarchive.molssi.org/admin_guide/setup.html)).
- Dataset submission searches for matching existing records or creates computations, while the server/worker
  architecture can execute those computations on local or HPC resources
  ([QCArchive dataset API](https://docs.qcarchive.molssi.org/user_guide/qcportal_reference/records/base_record_models.html);
  [compute managers](https://docs.qcarchive.molssi.org/admin_guide/managers/index.html)).
- A dataset view is a standalone SQLite file that can be loaded and queried without a server connection
  ([QCArchive dataset views](https://docs.qcarchive.molssi.org/user_guide/datasets/caching.html)).

Recommended correction: keep positive ratings for calculation provenance, API, and self-hosting; explicitly credit
calculation submission and user-created datasets. Do not equate dataset mutability with append-only semantics:
QCArchive dataset membership and metadata can change, and its official API includes record maintenance operations.
Likewise, do not call a calculation record scientifically rerunnable without checking whether the exact program,
environment, auxiliary files, and external dependencies required for that record are retained.

### ioChem-BD

ioChem-BD is currently underrated on contribution, API, and self-hosting:

- The official project describes a modular platform for data creation and curation, publication, storage, indexing,
  and search. It extracts labeled data from raw computational outputs and supports validation, enrichment,
  publication, sharing, post-processing, and visualization
  ([ioChem-BD overview](https://docs.iochem-bd.org/doc/what-is-iochem-bd.html)).
- Its API documentation covers a Create service for managing/uploading data, a Browse service for published
  computational data, and a Find service for discovery across public instances
  ([ioChem-BD API reference](https://docs.iochem-bd.org/api/)).
- Browse exposes collections, item metadata, file/bitstream metadata, and bitstream retrieval through REST
  ([ioChem-BD Browse API](https://docs.iochem-bd.org/api/browse.html)).
- The AGPL-licensed source is deployable, including via containers; public instances can join a federated central
  service with DOI and replicated-storage support
  ([ioChem-BD source repository](https://gitlab.com/ioChem-BD/iochem-bd);
  [installation guide](https://docs.iochem-bd.org/doc/guides/installation/installation.html)).

Recommended correction: positive ratings are warranted for community/institutional upload, REST API, raw file
retrieval, and self-hosting. Re-importable full-provenance archives, immutability semantics, and a distinct human
trust/selection layer were not established by the sources reviewed and should be marked “not established,” not
automatically absent.

### NIST Chemical Kinetics Database

The phrase “provenance largely as citations” loses important distinctions:

- NIST reports more than 38,000 separate rate records for more than 11,700 reactant pairs and returns all records
  for a matched reaction. A record can include modified-Arrhenius parameters, uncertainty, temperature and pressure
  ranges, bulk gas, data type (experimental, theoretical, modeling, and others), relative-rate links, structured
  experimental-procedure fields, and bibliography
  ([NIST Chemical Kinetics Database overview](https://kinetics.nist.gov/kinetics/welcome.jsp)).
- The current official landing page identifies SRD 17 Version 7.1, release 1.6.8, data version 2026 and offers
  reaction and bibliographic search
  ([NIST Chemical Kinetics Database](https://kinetics.nist.gov/kinetics/)).

Recommended correction: rate NIST positively for representing disagreement (multiple independent records) and
partially/positively for structured provenance under a stated definition. It does not preserve a complete
electronic-structure calculation graph or execution archive, but it stores considerably more than a bare citation.
The manuscript's bibliography currently says data version 2015.09; reconcile that with the live site's 2026 label
or cite an archived version explicitly.

### Active Thermochemical Tables

ATcT likewise exposes structured provenance concepts:

- ATcT constructs, analyzes, and solves a thermochemical network containing experimental and theoretical
  determinations. Its current site says the released network provides species-specific provenance analysis for more
  than 3,400 species, including influential determinations and correlated enthalpies
  ([ATcT home](https://atct.anl.gov/)).
- A species page can quantify the contributors to a value's provenance, report the original/assigned uncertainty,
  and link determinations onward to references and notes
  ([ATcT provenance example](https://atct.anl.gov/Thermochemical%20Data/version%201.202/species/?species_number=300)).

Recommended correction: do not characterize ATcT as a simple citation-backed table. It is a continuously developed
network-derived evaluation with an explicit provenance analysis and uncertainty model. It still differs from
TCKDB because the public surface reviewed here does not expose a rerunnable calculation archive or community upload
workflow.

### RMG Database and pressure-dependent kinetics

The manuscript's basic distinction—estimation rules and libraries versus accumulating calculation-linked product
records—is useful, but the RMG row needs more nuance:

- RMG provides searchable thermodynamics, transport, and kinetics databases and is open source
  ([RMG website](https://rmg.mit.edu/)).
- Kinetics libraries hold reaction-specific parameters, while families include training reactions, rules, group
  trees, and reaction recipes; library precedence is explicit
  ([RMG kinetics database](https://reactionmechanismgenerator.github.io/RMG-Py/users/rmg/database/kinetics.html)).
- RMG can extract whether kinetics came from rate rules, training, a library, or a pressure-dependent network, and
  can reconstruct estimated kinetics from recorded rate-rule/training provenance
  ([RMG `KineticsDatabase`](https://reactionmechanismgenerator.github.io/RMG-Py/reference/data/kineticsdatabase.html)).
- Arkane/RMG's live pressure-dependent service solves unimolecular networks using a one-dimensional master equation
  and returns fitted \(k(T,P)\)
  ([RMG pressure-dependent networks](https://rmg.mit.edu/pdep/)).
- Official database-modification instructions document a direct contribution path for new libraries/training
  content and support falloff, PLOG/PDepArrhenius, and Chebyshev representations
  ([RMG database modification](https://reactionmechanismgenerator.github.io/RMG-Py/users/rmg/database/modification.html)).

Recommended correction: credit RMG with structured source attribution, reconstructable rule-based estimates, an
open contribution path, and first-class pressure-dependent kinetics in its software/data ecosystem. The more
defensible contrast is that TCKDB normalizes multiple contributed *computed realizations and their evidence* under a
shared identity and review model; this must be demonstrated with populated examples.

### PrIMe

PrIMe should be treated as a substantive precursor, not only a broad aspiration:

- Its primary paper describes an automated data-centric infrastructure connected to collaborative workflows,
  heterogeneous records, scientific methods, uncertainty-aware inference, consistency evaluation, and
  “what-if” analyses
  ([You et al., 2012](https://doi.org/10.1002/kin.20627)).
- The foundational work explicitly framed PrIMe as process informatics for collaborative combustion data and
  model development ([Frenklach, 2007 record](https://firedoc.nist.gov/article/ingzXYQBWEcjUZEYci27)).

Recommended correction: compare TCKDB with PrIMe feature-by-feature in the Supporting Information, especially data
modeling, contribution/review, uncertainty, mechanisms, experiments versus calculations, and present operational
status. The present landscape table omits PrIMe despite relying on it in the prose.

### Frozen reaction/ML datasets

The statement that particular releases are snapshots is generally fair, but one aggregate “Frozen ML datasets” row
is too coarse:

- Transition1x is a fixed HDF5 release of 9.6 million \(\omega\)B97X/6-31G(d) force/energy calculations around
  reaction paths ([Transition1x primary preprint](https://arxiv.org/abs/2207.12858)).
- RGD1's author-hosted description reports 126,857 distinct reactions at B3LYP-D3/TZVP, including multiple
  transition-state conformations for many reactions, and explicitly notes a corrected earlier release
  ([RGD datasets](https://engineering.purdue.edu/savoiegroup/data%2Bcode.html)).

Recommended correction: assess each named dataset separately and distinguish “versioned/static distribution” from
“unable to evolve.” Versioned replacements and corrections do occur. Also separate “one level of theory per
release” from “one value per reaction”: RGD1, for example, includes multiple transition-state conformations for a
substantial subset.

## Important omitted comparators

The comparison set is not yet broad enough for a universal novelty claim:

- The Open Reaction Database has a structured Protocol Buffers schema, open Git history, public versioned
  snapshots, full-repository download, a community contribution path through pull requests, and governing review.
  Its scope excludes gas-phase kinetics and electronic-structure featurization, so it is not a direct substitute,
  but it is a strong comparator for community contribution, review, versioning, and ML-facing structured reaction
  data ([ORD overview](https://docs.open-reaction-database.org/en/stable/overview.html)).
- Materials Cloud should not be collapsed entirely into AiiDA: AiiDA is the provenance/workflow engine, while
  Materials Cloud supplies publication, DOI/archive, interactive exploration, and other data services.
- A publication-quality audit should also assess ChemKED for machine-readable combustion experiments and the
  current operational status and data access of PrIMe. These were not fully audited here.

## Specific changes recommended for Table 1

### Replace the table's method

1. Remove “To our knowledge, only TCKDB combines all eight” unless a systematic, reproducible search protocol and
   evidence appendix support it. Use: “Table 1 compares selected systems under the operational definitions in
   Table S1, based on official documentation accessed on YYYY-MM-DD.”
2. Replace author-impression symbols with per-cell evidence citations in the Supporting Information. Have at least
   two assessors independently score the table and report disagreements.
3. Use `yes / partial / no evidence found / out of scope`, not a dash that conflates absence, non-goal, and
   uninvestigated behavior.
4. Give every row a version or access date. Platforms evolve; the NIST and RMG versions in particular have changed.

### Split ambiguous columns

- Split **API + self-host** into `documented programmatic API` and `documented self-hosted deployment`.
- Split **Re-comp. from rec.** into:
  - inputs and method specification retained;
  - raw outputs/artifacts retained;
  - execution environment/dependencies retained;
  - archive re-import tested;
  - independent scientific rerun demonstrated within tolerance.
- Split **Comm. upload** into `authenticated contribution mechanism`, `public/community eligibility`, and
  `review/publication workflow`.
- Define **Append-only** at the record level. Distinguish mutable drafts, immutable publication, immutable
  calculation nodes, retained prior versions, and database-enforced non-deletion.
- Define **Trust layer** as three separate properties: evidence completeness, human review state/history, and
  representative-value selection. This is the most promising TCKDB differentiator, so it should not be compressed
  into one subjective symbol.
- Replace **Scope** with four explicit columns: thermochemistry, elementary kinetics, transport, and normalized
  pressure-dependent networks/products.
- Define **Represent disagreement** as multiple independently attributed scientific values under one normalized
  chemical identity—not simply multiple calculations or multiple literature records.

### Correct likely ratings

At minimum, based on the official evidence above:

| Resource | Corrections relative to the current manuscript |
|---|---|
| AiiDA | Positive for immutable stored nodes/files, provenance graph, re-importable provenance archives, API/self-host; product-level disagreement/trust remains unestablished. |
| NOMAD | Positive for authenticated upload, public immutable publication, raw and normalized API retrieval, transfer bundles, and self-hosting; distinguish preservation from rerun. |
| QCArchive | Positive for JSON API, self-hosting, calculation submission, user-created datasets, and offline views; append-only and product trust should be separately assessed. |
| ioChem-BD | Positive for upload/curation/publication, raw files, REST APIs, federation, and self-hosting; full archive rerun and trust-selection separation remain unestablished. |
| NIST kinetics | Positive for multiple attributed records/disagreement; at least partial for structured provenance, uncertainty, validity, and procedure—not merely citations. |
| ATcT | Positive/partial for explicit network-derived provenance and uncertainty; no evidence here for rerunnable calculation archives or open community upload. |
| RMG | Positive/partial for structured source attribution, reconstructable estimates, community database modification, broad kinetics representations, and pressure-dependent tooling. |
| Frozen ML datasets | Replace aggregate row with named/versioned datasets; do not equate fixed release packaging with one value per identity or inability to issue corrections. |

TCKDB's own checkmarks should be held to the same evidence standard. In particular, a design or schema is not yet
evidence of community operation, public artifact retrieval, immutable production records, a successful independent
rerun, or a populated pressure-dependent corpus.

## Claims that can be retained with narrower wording

- “TCKDB is designed specifically for gas-phase thermochemistry and kinetics products linked to calculation-level
  evidence” is well differentiated from broad materials archives and calculation orchestrators.
- “TCKDB separates evidence completeness, human review, and representative selection” is a potentially strong
  contribution if each mechanism exists, is queryable, and is demonstrated end to end.
- “TCKDB complements rather than replaces ATcT, NIST, RMG, QCArchive, NOMAD, AiiDA, and ioChem-BD” is appropriate.
- “Selected static reaction datasets generally distribute versioned, level-of-theory-specific snapshots rather than
  operate community review services” is supportable when stated per named dataset.

Avoid “no comparable resource,” “only TCKDB,” or “all existing resources” without a registered review protocol and
much broader search.

## Research gaps before submission

1. Audit actual public instances and APIs, not documentation alone, using a dated test script and archived response
   metadata.
2. Establish whether exact executable environments and auxiliary files are retained by QCArchive, NOMAD, AiiDA,
   and ioChem-BD for representative records; do not infer rerunnability from provenance metadata.
3. Assess deletion/supersession/version-history semantics in QCArchive, ioChem-BD, RMG, NIST, and ATcT at both
   application and database levels.
4. Audit the current PrIMe service/data availability and include PrIMe in the table.
5. Add ChemKED and the Open Reaction Database; consider KIDA/UMIST and ReSpecTh if the claimed scope extends to
   gas-phase kinetic community resources.
6. Define and test “human-reviewed” consistently. Editorial publication, repository moderation, PR review,
   scientific peer review, and per-record curator approval are not interchangeable.
7. Evaluate pressure-dependent data as a populated, queryable corpus, not merely whether software can calculate or
   represent Chebyshev/PLOG/falloff forms.
8. Preserve the comparison evidence in the paper's release archive, including source URLs, access dates, platform
   versions, scoring definitions, assessor decisions, and unresolved cells.
