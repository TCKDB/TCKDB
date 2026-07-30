# Execution environment manifests

`tckdb.execution-environment.v1` is a typed, secret-free, immutable record of
the environment a calculation ran in. It accepts **four** runtime variants at
two tiers, and the tier is readable from `runtime_kind`.

The `described` tier records a named-but-unpinned environment:

- `described`: a short human description plus optional module names/versions.
  No digests, and `closure` must be empty. This is the ordinary shared-cluster
  case and it is fully acceptable — `module load gaussian/16` against a site
  install is real, comparable provenance even though it fixes no bytes.

The `content_addressed` tier pins the environment by bytes:

- `container`: an OCI image reference containing an exact
  `@sha256:<64 lowercase hex>` digest. Tags, including non-`latest` tags, are
  never accepted. The image digest is the dependency closure.
- `conda`: an exact content-addressed lock/explicit-environment file plus an
  exact content-addressed executable. A conda-lock is generated per platform,
  so the lockfile digest already pins the platform.
- `hpc_module`: module names/versions are descriptive only. A resolved module
  environment digest and a dependency-manifest digest are both mandatory, as
  is an exact executable digest.

The executable digest is optional in the `described` tier, because most people
run a shared binary they have never hashed, and required in every pinned tier,
because a byte closure that omits the executable is not a closure.

There are deliberately **no** `platform`/`architecture` fields. They were coarse
three-value enums that could not distinguish AVX2 from AVX-512, pin glibc, or
name the linked BLAS — so they constrained nothing that matters — while
duplicating what the closure already implies: a conda-lock is per-platform, and
a platform-specific OCI manifest carries the same fact. Nothing cross-validated
the two copies, which is precisely how a derived value with a second
un-arbitrated home comes to disagree with its source. Every locator rejects
credentials, URI userinfo/query/fragment, and secret-bearing text; every
closure role and locator is unique. The server canonicalizes the role-sorted
payload and hashes it; `sha256:<digest>` is the public content reference.

The manifest is optional and **is not graded**. A calculation without one
uploads normally and can still reach `rerunnable`, because that grade measures
whether the deposited evidence is complete enough to attempt a rerun. The
manifest is recorded as additive provenance under
`context_json['execution_environment']`, with a `revalidates` flag reporting
whether the stored row still agrees with its own digest — a storage-drift
signal, not a judgement about the uploader.

See `reproducibility_assessments.md` for why: the values that determine a number
(energy corrections, frequency scale factors, level of theory, parameters) are
already stored as typed rows, which is stronger evidence than a pointer to an
environment that could regenerate them.

Its real value is discrimination: two records claiming the same
`software_release` but carrying different manifest digests ran in different
environments, and that is worth surfacing.

Scientific calculation reads keep this block opt-in via
`include=execution_environment`. Default response shapes remain unchanged;
`available_sections.has_execution_environment` says whether the linked stored
row revalidates. Corrupt rows fail closed: they are neither projected nor
advertised as available.
