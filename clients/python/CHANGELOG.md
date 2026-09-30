# Changelog

## 0.99.0 - 2026-09-30

A single atom may anchor its conformer on an `sp` (#610), matching
`tckdb-schemas` 0.59.0, which now requires `>=0.59.0`. `ComputedSpeciesUpload`
and `ComputedReactionUpload` no longer refuse a non-`opt` primary
calculation outright: they apply the schema's own rule to the conformer
geometry they will send, so an `sp` primary is accepted when that geometry is
one atom, and refused (with the atom count) otherwise. With no explicit
`primary_calculation=` and no `opt`, the first `sp` is the candidate. Anything
with two or more atoms is unchanged. Producers should stop relabelling an
atom's single point as an `opt`.

## 0.98.0 - 2026-09-30

Adds `RejectionCode.BUNDLE_TOO_LARGE` (HTTP 413) and
`RejectionCode.BUNDLE_TOO_MANY_RECORDS` (HTTP 422). `POST /api/v1/bundles/dry-run`
and `/submit` now refuse a bundle whose request body exceeds the server's
`BUNDLE_MAX_BODY_BYTES` (default 5 MiB) or that carries more than
`BUNDLE_MAX_RECORDS` (default 500) thermo plus kinetics records; split the
bundle. The dry run also has a tighter rate limit than uploads.

## 0.97.0 - 2026-09-29

Artifact uploads address a calculation by its `calc_` ref. Upload results now
carry `calculation_ref` beside `calculation_id` (and `calculation_key_refs`
beside `calculation_keys` on a computed reaction), and `submission_ref` beside
`submission_id`. `PlannedArtifactUpload` gains an optional `calculation_ref`,
filled from those responses, and `upload_artifacts` / `upload_artifact` send it
in the path when they have one, falling back to the integer against a server
that predates it. `upload_artifact` now accepts either form. Nothing existing
is removed: plans built without refs behave as before.

Retry caveat: idempotency keys are scoped by URL path. An artifact upload
committed by 0.96 through `/calculations/{integer}/artifacts` whose response
was lost, then retried by 0.97 with the same key, goes to the `calc_` path, is
not treated as a replay, and attaches duplicate artifacts. Finish in-flight
uploads before upgrading.

## 0.96.0 - 2026-09-29

Adds `RejectionCode.SUBMISSION_SUPERSEDE_NOT_OWNER` (HTTP 403). The server now
refuses `POST /api/v1/submissions/{submission_ref}/supersede` unless the caller
created both submissions or holds the curator or admin role. The client has no
typed method for this route; a raw `post_json` caller can branch on the code.

## 0.95.1 - 2026-09-29

The parity table follows the server renaming the submission supersede route
(issue #571): it is now `POST /api/v1/submissions/{submission_ref}/supersede`,
and its body is `{"new_submission_ref": "sub_..."}`. Both submissions are named
by their public ref (`public_ref` on a submission read), and a row id is
refused with 422 in the path and in the body. The client has no typed method
for this route and never sent the integer, so nothing here changes behaviour;
a raw `post_json` caller must send the refs.

## 0.95.0 - 2026-09-29

A 2xx response that is not the API's answer now raises the new
`TCKDBUnexpectedResponseError` instead of being returned (issue #568). Before,
a JSON endpoint that answered 200 with a body that is not JSON handed that
body back as a string in `data`; the usual cause is a `base_url` naming the
site root (`https://host`) rather than the API root (`https://host/api/v1`),
where the web app answers every path with its HTML page, and callers then
failed later with a bare `TypeError`. The message names the request
(`GET https://host/scientific/calculations/... returned HTTP 200 with an
HTML page`) and, for HTML, the base URL to use instead. The client still
does not append `/api/v1` itself: `base_url` is documented as the API root.

`TCKDBUnexpectedResponseError` subclasses `TCKDBHTTPError`, so existing
`except TCKDBHTTPError` handlers catch it; it carries `url`, `content_type`,
`status_code`, `response_text` and `headers`, and `code` is `None` because
no server sent one. The NDJSON and Chemkin exports refuse an HTML 200 the
same way. `download_artifact` does not: the server labels a download by its
stored filename, so an uploaded `.html` file is a legitimate `text/html` 200.
An empty success body (204) is still `data=None`.

## 0.94.0 - 2026-09-29

`RejectionCode.CALCULATION_SOFTWARE_IS_WORKFLOW_TOOL` now also covers ARC
and RMG (`RMG-Py`), not only Arkane: the server refuses a calculation whose
`software_release.name` is any of them (issue #305, owner decision), in any
case or spelling, with or without a trailing version (`ARC 1.1.0`,
`ARC-1.1.0`, `rmgpy`, `rmg_py`, `RMG Py`, `RMG-Py 3.2.0`). A
calculation's software is the electronic-structure program that ran the job;
put ARC in `workflow_tool_release`. No enum member changed. A product's
`analysis_software_release` is still unaffected, so RMG remains valid there.

The server also now fills a missing version from an uploaded output log: a
calculation declared with no `software_release.version` whose `output_log`
banner names the same program with a version is re-pointed at that versioned
release, and the upload returns an informational
`software_release_version_filled_from_artifact` warning.

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
