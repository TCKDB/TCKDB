# Tests and non-goals

## Tests

```bash
pip install -e "./clients/python[test]"   # from the repository root
pytest
python -m py_compile examples/basic_usage.py examples/upload_json_file.py examples/submit_bundle.py
```

The test suite uses `httpx.MockTransport` and never contacts a live TCKDB
instance.


## Non-goals (v0)

- no chemistry adapters / no ARC, RMG, RDKit, ASE, cclib, Arkane imports
- no async client (deferred)
- no automatic retries
- no payload sidecar / on-disk replay management
- no OpenAPI-generated client code
- no direct database access
