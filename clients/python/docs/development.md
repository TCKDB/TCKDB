# Tests and non-goals

## Tests

```bash
pip install -e "./clients/python[test]"   # from the repository root
pytest
python -m py_compile examples/basic_usage.py examples/upload_json_file.py examples/submit_bundle.py
```

The test suite uses `httpx.MockTransport` and never contacts a live TCKDB
instance.


## The `tckdb-schemas` window

`tckdb-client` depends on `tckdb-schemas` with a rolling window, currently `>=0.158,<0.167`.

- The floor is the schemas release the client release was built and tested with.
- The ceiling is five minors above the floor. `tckdb-schemas` is 0.x and a minor may break, so the window is a promise about releases we have not seen, not a guarantee.
- Every client release moves the window up with the schemas line it was tested against.
- A published client keeps its ceiling forever; pip metadata cannot be edited after upload. So a schemas minor that breaks the client must not fall inside the window of any published client release. If one would, either yank the client releases whose window covers it, or hold the break until those windows have passed. Releasing a new client with a lower ceiling only helps new installs; existing releases still accept the breaking schemas.
- `backend/tests/test_release_metadata.py` fails when the in-repo schemas version reaches the ceiling or drops below the floor, so the window gets moved in the same change that bumps schemas.

## Non-goals (v0)

- no chemistry adapters / no ARC, RMG, RDKit, ASE, cclib, Arkane imports
- no async client (deferred)
- no automatic retries
- no payload sidecar / on-disk replay management
- no OpenAPI-generated client code
- no direct database access
