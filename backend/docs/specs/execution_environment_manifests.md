# Execution environment manifests

`tckdb.execution-environment.v1` is a typed, secret-free, immutable closure
for one calculation. It accepts exactly three runtime variants:

- `container`: an OCI image reference containing an exact
  `@sha256:<64 lowercase hex>` digest. Tags, including non-`latest` tags, are
  never accepted. The image digest is the dependency closure.
- `conda`: an exact content-addressed lock/explicit-environment file plus an
  exact content-addressed executable. The declared platform and architecture
  are enumerated fields, not free text.
- `hpc_module`: module names/versions are descriptive only. A resolved module
  environment digest and a dependency-manifest digest are both mandatory, as
  is an exact executable digest.

Bare-metal and VM descriptions are deliberately unsupported until they have an
equally closed image or lockfile representation. Every locator rejects
credentials, URI userinfo/query/fragment, and secret-bearing text; every
closure role and locator is unique. The server canonicalizes the role-sorted
payload and hashes it; `sha256:<digest>` is the public content reference.

The manifest is optional, so a calculation without one still uploads and still
reaches `auditable`. `tckdb_reproducibility` awards `rerunnable` only to a
calculation whose other checks pass and whose current manifest closure
validates. This means the saved
calculation can be re-executed from its preserved evidence; it does not promise
bit-identical numerical output, licensed software availability, scheduler
availability, or recomputation of thermo/kinetics/statmech/transport/PDep
products without their own derivation recipes and source-role closure.

Scientific calculation reads keep this block opt-in via
`include=execution_environment`. Default response shapes remain unchanged;
`available_sections.has_execution_environment` says whether the linked stored
row revalidates. Corrupt rows fail closed: they are neither projected nor
advertised as available.
