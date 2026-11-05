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

`tckdb-client` depends on `tckdb-schemas` with a rolling window, currently `>=0.155,<0.160`.

- The floor is the schemas release the client release was built and tested with.
- The ceiling is five minors above the floor. `tckdb-schemas` is 0.x and a minor may break, so the window is a promise about releases we have not seen, not a guarantee.
- Every client release moves the window up with the schemas line it was tested against.
- A schemas minor that breaks the client must not fall inside a window that is already published. If one would, the client is released with a ceiling below it first, or the break waits for a major.

## Non-goals (v0)

- no chemistry adapters / no ARC, RMG, RDKit, ASE, cclib, Arkane imports
- no async client (deferred)
- no automatic retries
- no payload sidecar / on-disk replay management
- no OpenAPI-generated client code
- no direct database access
