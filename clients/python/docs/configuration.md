# Configuring the client

Moved from the package README. The README keeps only the two environment variables.

## Configure

Two values fully determine the target instance:

```bash
export TCKDB_BASE_URL="http://localhost:8010/api/v1"
export TCKDB_API_KEY="tck_replace_me"  # optional for public scientific reads
```

API keys are minted on the **target instance**. Pointing at a different
`base_url` does not migrate or sync data — see
[`docs/clients/generic-client-targeting.md`](../../../docs/clients/generic-client-targeting.md)).

### When is the API key required?

| Operation | API key required client-side? |
|-----------|-------------------------------|
| Scientific reads (`search_*`, `get_species_thermo`, `get_reaction_*`, `get_geometry`) | **No** — anonymous-friendly. Set a key only if the deployment requires authentication or you want authenticated quotas. |
| `health()` | No |
| `me()`, uploads, bundle dry-run/submit, `post_json`, `get_json` | **Yes** — the client raises `TCKDBAuthenticationError` before sending. |

When an API key *is* configured, every request — including scientific
reads — forwards it as the `X-API-Key` header. That lets authenticated
deployments bill reads against a user identity.

> The client is not an abuse-control boundary. Hosted deployments
> should enforce abuse limits server-side through rate limits,
> pagination caps, query timeouts, and monitoring.
