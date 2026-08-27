# Raspberry Pi frontend deployment

The public website is a Vite/React bundle served by a small nginx container.
It is independent from the FastAPI deployment: the frontend has no database
migrations and never reads `.env.pi`.

## Topology

```
browser
  -> Nginx Proxy Manager (TLS, public hostname)
  -> 127.0.0.1:8011  tckdb-frontend (nginx)
       -> static SPA files
       -> /api/* over tckdbv2_default -> tckdb-api:8010
```

The frontend nginx configuration returns `index.html` for non-file routes,
so direct visits to React Router paths such as `/species/CH3` work. Browser API
requests remain same-origin at `/api/...`; nginx proxies them to the existing
private `tckdb-api` container over the Docker network.

## First-time host setup

Do this as an explicit host-operation decision; it is not automated by CI.
The API container and the `tckdbv2_default` network must already exist.

1. Copy `frontend/scripts/ops/tckdb_frontend_deploy.sh` to the Pi checkout
   beside the existing backend deploy script, and mark it executable.
2. Deploy an immutable image tag. Derive it rather than typing it:

   ```bash
   ./tckdb_frontend_deploy.sh sha-$(git rev-parse origin/main)
   ```

3. Only after the script reports a verified frontend deploy, in Nginx Proxy
   Manager change the `tckdb.homecalvin.com` upstream from `127.0.0.1:8010`
   to `127.0.0.1:8011`. Keep TLS and the hostname unchanged. If the public
   verification below fails, immediately switch it back from `8011` to `8010`
   (the API remains the known-good public upstream).
4. Verify a direct route and the API through the public hostname:

   ```bash
   curl -fsS https://tckdb.homecalvin.com/species/CH3 | grep -q '<div id="root">'
   curl -fsS https://tckdb.homecalvin.com/api/v1/status \
     | jq -e '.status == "ok" and (.degraded | type == "array") and .degraded == []'
   ```

The script accepts only `sha-<40-character-commit>` tags. It pulls before
stopping the current frontend, preserves its exact container configuration,
and automatically restores it if the candidate fails to start or fails root,
SPA-route, or proxied API-status health checks. It binds its port to loopback
only and reports the running image's repository digest with `--check`; it does
not access the database or `.env.pi`.

## Subsequent frontend deploys

The `build-frontend-image` workflow publishes `laxzal/tckdb-frontend` for
`linux/amd64` and `linux/arm64` when `frontend/**`, its deployment document, or
its workflow changes on `main`. The workflow smoke-tests an amd64 image's SPA
fallback and `/api/` proxy before publishing the multi-architecture manifest.

After that workflow succeeds, run the same script with the main commit SHA,
then make the same public SPA and `status/degraded` checks above. Do not use
`latest`: it moves and makes the served bundle unanswerable.
