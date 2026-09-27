# Changelog

## 0.93.0 - 2026-09-28

Adds `RejectionCode.CALCULATION_SOFTWARE_IS_WORKFLOW_TOOL` (HTTP 422). The
server now refuses a calculation whose `software_release.name` is a workflow
tool (Arkane): a calculation's software is the electronic-structure program
that ran it. Declare that program instead, and put Arkane in
`workflow_tool_release` if it orchestrated the job. A thermo, statmech or
kinetics record's `analysis_software_release` is unaffected (issue #305).

## 0.92.0 - 2026-09-27

Requires `tckdb-schemas>=0.51.0`. The thermo builder (`Thermo`,
`Thermo.points`) now refuses tabulated Gibbs energies (`g_kj_mol`) without
`enthalpy_reference_kind`, with `enthalpy_declaration_absent`, and accepts
them when `formation_298k` is declared, even with no point enthalpy. A
stored G sits on the record's enthalpy zero, so it needs the same
declaration an enthalpy does. This matches the server, which applies the
same shared rule; with an older `tckdb-schemas` the builder would pass a
deposit the server refuses.

## 0.91.0 - 2026-09-23

**Breaking:** `tckdb` (the `get reaction`/`download artifact` CLI) no longer
defaults `--base-url` to a hardcoded deployment. Pass `--base-url`, or set
`TCKDB_BASE_URL`; omitting both is now a clear error naming both ways to
supply it, instead of a silent request to that host. If you relied on the
default, add `--base-url` or `TCKDB_BASE_URL` to your invocation. See issue
#521.

## 0.89.0 - 2026-09-23

Explicit enthalpy reference declarations for thermo deposits and grouped reference reads.
No inferred defaults or legacy backfill.
