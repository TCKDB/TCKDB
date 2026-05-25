# Uploads

Uploads require an API key. Anonymous users can read scientific data on
default deployments, but writes are authenticated.

```bash
curl -X POST "$TCKDB_BASE_URL/uploads/calculations" \
    -H "X-API-Key: $TCKDB_API_KEY" \
    -H "Content-Type: application/json" \
    --data-binary @my_calculation_payload.json
```

## What Gets Uploaded

TCKDB uploads are structured scientific JSON payloads. Raw Gaussian,
ORCA, RMG, ARC, or other workflow files can be stored as artifacts, but
the database records still need parsed scientific content:

- species and species entries
- reactions and reaction entries
- calculations and dependencies
- levels of theory
- software and workflow provenance
- geometries
- statmech, thermo, kinetics, transport, or network records
- artifact handles for raw logs or larger files

A workflow adapter is usually responsible for turning tool output into
the TCKDB upload shape.

## Useful References

- [Admin auth quickstart](../deployment/admin_auth_quickstart.md)
- [TCKDB client v0 spec](../specs/tckdb-client-v0-spec.md)
- [ARC adapter spec](../specs/arc-tckdb-adapter-v0-spec.md)
- [Contribution bundle format](../contribution-bundles/v0-format.md)
