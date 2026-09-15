# Trimmed payload (task #264)

The payload in this directory is a **derived copy** of the corresponding file
under `../tckdb_payloads/computed_reaction/`, the real payload ARC produced.
It is not itself captured producer output.

**What was removed:** exactly one entry from
`transition_state.applied_energy_corrections` -- a `bac_total` correction
with `value: 0.0` and no `components`. That shape is a false statement (a
saddle point carries no bond assignment, so a componentless zero
bond-additivity correction there can never be real; see
`backend/alembic/versions/a55cc983501a_repair_false_zero_ts_bac_totals.py`
and `backend/app/services/energy_correction_resolution.py`) and the upload
API now refuses it with `bac_total_requires_components`.

**Why the original is kept beside it, unmodified:** the original is the only
in-repo evidence of what a real producer actually emits, including this
defect, and `tests/api/test_api_arc_run_fixtures.py::test_arc_run_payload_uploads_cleanly`
asserts against the original that this exact shape is now refused (422).

**Why this trimmed copy exists at all:**
`test_arc_runs_aggregate_conformer_consolidation` uploads every scenario in
one pass to prove that independent ARC runs of the same species consolidate
onto one `conformer_group`. That property has nothing to do with this
scenario's transition-state-side energy correction, and the whole bundle
upload is all-or-nothing, so the *original* payload (now correctly refused)
cannot be used there without breaking a test whose subject is unrelated
consolidation behavior. This copy removes only the refused entry -- nothing
else differs -- so the upload succeeds and the consolidation assertions
still exercise everything else about a real ARC bundle.
