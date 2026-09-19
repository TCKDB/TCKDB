# ThermoML and MolSSI interchange for TCKDB

Assessed 2026-09-19 against primary documentation. This recommends future adapters; it does not establish existing TCKDB integration.

## Recommendation

Support **two complementary scientific interchange adapters**: ThermoML for thermophysical/thermochemical property evidence, and MolSSI QCSchema for quantum-chemistry calculation inputs and results. Keep TCKDB's internal domain model and API; standards should provide validated import/export boundaries. Supporting XML serialization alone would not establish scientific interoperability.

| Component | What it is | Recommended TCKDB role |
|---|---|---|
| ThermoML | XML-based IUPAC thermodynamic-property exchange standard. | Exchange reported property observations, conditions, uncertainties, and bibliographic context. [NIST](https://www.nist.gov/mml/acmd/trc/thermoml) |
| QCSchema | MolSSI JSON schema for molecular structures and quantum-chemistry inputs/outputs. | Exchange calculation evidence without requiring a separate text-output parser for every producer. [MolSSI](https://molssi.org/software/qcschema-2/) |
| QCElemental | Python implementation of QCSchema models, validation, and serialization. | Validate supported QCSchema versions at the adapter boundary. [Model documentation](https://molssi.github.io/QCElemental/dev/models.html) |
| QCArchive | Calculation-management platform comprising QCFractal, QCPortal, and computational workers. | Optional source connector using the supported client; retain server/dataset/record references. It is a service ecosystem, not another XML format. [QCArchive documentation](https://docs.qcarchive.molssi.org/) |

## ThermoML adapter

Begin with an explicitly documented subset of pure-compound molecular properties. Preserve source compound identifiers, property definition, phase, temperature, pressure, units, uncertainty meaning, experimental method, and citation. Introduce mixture composition and other richer contexts only when TCKDB can represent them without semantic loss. ThermoML recommendations cover pure compounds, mixtures, reactions, uncertainty, predicted/evaluated data, and equation representation; implementing one subset does not justify claiming complete support. [IUPAC recommendations](https://trc.nist.gov/TML/ThermoMLRecommendations.pdf)

Archive ingestion should use the available XML/JSON distributions and metadata search interfaces. The archive already offers JSON and versioned bulk files, so XML parsing need not be the only access route. Validate actual ThermoML exports against the pinned XSD. [Archive](https://trc.nist.gov/ThermoML/Browse), [NIST archive architecture](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=933505)

Do not assign TCKDB approval automatically because a record originated at NIST. NIST explicitly distinguishes checking archived values/metadata from critical evaluation. Preserve the source's evidence and evaluation status separately from TCKDB review. [NIST ThermoML description](https://www.nist.gov/mml/acmd/trc/thermoml)

## QCSchema adapter

Prioritize molecular single-point energy, gradient, and Hessian results, then optimization procedures and their trajectories. Map geometry, atom ordering, isotopic masses, charge, spin multiplicity, method/basis, program settings, result type, success/error state, and producer provenance. QCSchema separates calculation input, primary results, and program/version/routine provenance; an energy result is not a thermochemical product. [Specification components](https://molssi-qc-schema.readthedocs.io/en/latest/spec_components.html)

Check coordinate and derivative units explicitly: the documented molecular geometry uses bohr. Preserve fragment and ghost-atom distinctions; route unsupported cases to an explicit rejection or unresolved mapping rather than creating an incorrect molecular identity. Do not silently infer bond connectivity as though the source asserted it. [Molecular schema](https://molssi-qc-schema.readthedocs.io/en/latest/auto_topology.html)

A QCSchema electronic energy does **not** establish formation enthalpy, entropy, heat capacity, or a rate coefficient. Those require additional references, statistical-mechanical assumptions, corrections, and derivations. TCKDB should connect imported calculations to separately attributed thermo/kinetics products when that evidence is supplied.

Version support must be explicit. QCElemental's development documentation describes a QCSchema v1/v2 transition and notes that its implementation is ahead of the separate specification repository. A molecular `schema_version=2` can belong to the older schema family; the newer molecule uses version 3. Record the model family, schema name/version, exact validator release, and adapter version. Do not select a schema from the integer alone or depend on whatever `latest` happens to mean. [QCElemental version guidance](https://molssi.github.io/QCElemental/dev/models.html)

## Shared contract and publication demonstration

Both adapters should retain original payload bytes with a digest, source/version/access information, and a mapping report listing transformed, retained-only, unsupported, and rejected fields. Keep schema validation separate from scientific mapping validation. A valid source document can still contain a state, species, or uncertainty representation the destination cannot express. Raw retention preserves evidence; it does not make the normalized mapping lossless.

For the first paper, demonstrate one externally sourced ThermoML subset and one independently generated QCSchema calculation bundle. Publish pinned source files, schemas, validator environments, mapping tables, and import/export scripts. Verify property conditions and uncertainty semantics for ThermoML; verify geometry, atom indexing, charge/spin, energies/derivatives, and provenance for QCSchema. Include malformed and unsupported cases and disclose any export losses. Claim interoperability only for that tested profile.

Prioritize QCSchema for the existing computational focus, while designing a bounded ThermoML pilot for the experimental bridge. Full cross-domain support and a QCArchive connector are separate milestones.

Audit scope: documentation only. GitNexus impact on this new path returned `UNKNOWN` from the existing storage-version mismatch (index 43; engine 42); no caller/process conclusion was drawn. Text search found no existing reference to the path. No existing report or code was changed.
