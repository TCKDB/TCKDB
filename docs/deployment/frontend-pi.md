# Raspberry Pi frontend deployment

The public website is a Vite/React bundle served by a small nginx container.
It is independent from the FastAPI deployment: the frontend has no database
migrations and never reads `.env.pi`.

## Topology

```
browser
  -> Cloudflare Tunnel
  -> cloudflared systemd service
  -> /etc/cloudflared/config.yml ingress
  -> 127.0.0.1:8011 tckdb-frontend (nginx)
       -> static SPA files
       -> /api/* over tckdbv2_default -> tckdb-api:8010
```

The frontend nginx configuration returns `index.html` for non-file routes,
so direct visits to React Router paths such as `/species/CH3` work. Browser API
requests remain same-origin at `/api/...`; nginx proxies them to the existing
private `tckdb-api` container over the Docker network.

## First-time host setup and ingress cutover

Do this as an explicit host-operation decision; it is not automated by CI.
The API container and the `tckdbv2_default` network must already exist.
On the production Pi, the public TCKDB path is the host `cloudflared` systemd
service, using `/etc/cloudflared/config.yml`; Nginx Proxy Manager is not part
of this path.

1. Preflight the existing API, Docker network, and tunnel service. Do not
   print the cloudflared configuration: it can contain tunnel credentials.

   ```bash
   docker inspect tckdb-api >/dev/null
   docker network inspect tckdbv2_default >/dev/null
   systemctl is-active --quiet cloudflared
   cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
   ```

2. On the operator workstation, from a clean, current local checkout on
   `main`, copy the tracked helper to the Pi's operator checkout (creating its
   destination directory if necessary), then invoke it remotely with the same
   immutable commit SHA. `/home/calvin/repos/tckdbv2` on the Pi is a stale,
   detached checkout used for operator files; it is not the running source.
   This command does not pull or deploy from that checkout:

   ```bash
   git fetch origin main \
     && test "$(git branch --show-current)" = main \
     && test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" \
     && git diff --quiet -- frontend/scripts/ops/tckdb_frontend_deploy.sh \
     && git diff --cached --quiet -- frontend/scripts/ops/tckdb_frontend_deploy.sh \
     && deploy_sha="$(git rev-parse HEAD)" \
     && ssh -o BatchMode=yes calvin@100.85.114.78 \
       "mkdir -p /home/calvin/repos/tckdbv2/frontend/scripts/ops" \
     && scp -p frontend/scripts/ops/tckdb_frontend_deploy.sh \
       calvin@100.85.114.78:/home/calvin/repos/tckdbv2/frontend/scripts/ops/ \
     && ssh -o BatchMode=yes calvin@100.85.114.78 \
       "cd /home/calvin/repos/tckdbv2 && frontend/scripts/ops/tckdb_frontend_deploy.sh sha-${deploy_sha}"
   ```

   The local `git fetch` only refreshes the operator's reference to `main`;
   neither it nor a `git pull` in the Pi checkout deploys a container.

3. Back up the ingress configuration before editing it. Give the backup a
   timestamp so it is not mistaken for a current configuration.

   ```bash
   stamp=$(date +%Y%m%d-%H%M%S)
   sudo cp --preserve=mode,ownership,timestamps /etc/cloudflared/config.yml \
     "/etc/cloudflared/config.yml.tckdb-frontend-${stamp}.bak"
   ```

4. Only after the frontend script reports a verified deploy, use `sudoedit`
   to change the existing `tckdb.homecalvin.com` ingress stanza's `service`
   from `http://127.0.0.1:8010` to `http://127.0.0.1:8011`. Preserve the
   tunnel credentials and every other ingress stanza. Validate and restart:

   ```bash
   cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate \
     && sudo systemctl restart cloudflared \
     && systemctl is-active --quiet cloudflared
   ```

5. Verify a direct SPA route and a healthy API through the public hostname:

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

## Roll back the ingress cutover

If the public checks fail after switching the tunnel to the frontend, return
the `tckdb.homecalvin.com` ingress stanza to `http://127.0.0.1:8010`, validate
the configuration, and restart `cloudflared`:

```bash
sudoedit /etc/cloudflared/config.yml
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate \
  && sudo systemctl restart cloudflared \
  && systemctl is-active --quiet cloudflared
```

Then confirm the known-good API public path before investigating the frontend:

```bash
curl -fsS https://tckdb.homecalvin.com/api/v1/status \
  | jq -e '.status == "ok" and (.degraded | type == "array") and .degraded == []'
```

The timestamped backup from the cutover is an additional recovery aid. Do not
copy it over the active configuration without first confirming it is the
backup created for this change and still has the intended tunnel settings.

## Subsequent frontend deploys

The `build-frontend-image` workflow publishes `laxzal/tckdb-frontend` for
`linux/amd64` and `linux/arm64` when `frontend/**`, its deployment document, or
its workflow changes on `main`. The workflow smoke-tests an amd64 image's SPA
fallback and `/api/` proxy before publishing the multi-architecture manifest.

After that workflow succeeds, run the same script with the main commit SHA,
then make the same public SPA and `status/degraded` checks above. Do not use
`latest`: it moves and makes the served bundle unanswerable.
