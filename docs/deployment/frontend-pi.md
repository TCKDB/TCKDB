# Frontend on the Pi: site config

The web front end is deployed with `frontend/scripts/ops/tckdb_frontend_deploy.sh sha-<commit>`. The image is shared by every deployment, so anything specific to one site lives in an operator file, not in the image.

## `site-config.json`

An optional JSON file that names the example record on the home page:

```json
{"homeExampleRef": "spe_..."}
```

The value is a molecule entry ref (`spe_...`) or a reaction entry ref (`rxe_...`). When the file is missing, malformed, or names a record the archive does not return under the current profile, the home page shows a "Browse molecules" link instead.

- **Default path:** `.site-config.json` at the root of the repo checkout the script runs from (gitignored). If it exists, a routine redeploy mounts it, so the example survives.
- **Another path:** set `TCKDB_FRONTEND_SITE_CONFIG=/absolute/path/site-config.json`. It must be a regular file (not a symlink), with no `,` or `:` in the path.
- **Permissions:** the file must be readable by others (`chmod o+r`), because nginx in the container runs as uid 101. The script refuses a file without o+r.
- **Mount:** read-only bind mount at `/usr/share/nginx/html/site-config.json`.
- **Validation:** the script checks the file on the host before deploying and checks the served `/site-config.json` after the health check. This needs `jq` or `python3`; with a config file present and neither installed, the script stops and says so. Contents are never printed.
- **A bad file blocks deploys** until it is fixed (a host-side failure stops before anything changes; a served-file failure restores the previous container).
- **Editing:** a single-file bind mount keeps the old file until the container restarts. After an edit, redeploy or run `docker restart tckdb-frontend`.
- **No file:** nothing is mounted, and the server answers 404 for `/site-config.json`.

Development: put the same JSON in `frontend/public/site-config.json` (gitignored; never copied into the image).
