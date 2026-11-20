# Which setup do I need?

TCKDB is one application. A laptop, a lab server and a public instance run
the same images, schema, API and login model. Two things differ: how clients
reach the server, and the settings in the env file.

<figure class="tk-figure">
--8<-- "assets/what-runs-where.svg"
<figcaption>The same application everywhere. Only the ingress and the env file differ.</figcaption>
</figure>

## Pick one

| You want | `DEPLOYMENT_MODE` | Reached at | Follow |
|---|---|---|---|
| To try TCKDB or work on it alone | `local` | `127.0.0.1` | [Self-host](README.md) with `--mode local`, or [Run from source](local-v0.md) to change the code |
| A private instance for a lab or group | `shared_private` | the lab network, a VPN or an internal proxy | [Self-host](README.md), then [Lab server](shared-private-deployment.md) |
| A public instance that anyone can read | `hosted_public` | a public HTTPS hostname | [Self-host](README.md), then [Public server](self_hosted_single_node.md) |
| To upload from cluster jobs | none: jobs are clients | | [Clients on HPC](client-access-from-hpc.md) |

No domain or internet connection is needed for `local` or a lab network. A
domain is needed only for a stable public address.

Clients use the same two values everywhere: a base URL and an API key. See
[Configuring a client](../clients/generic-client-targeting.md).

## What the mode changes

- **`local`** allows the developer defaults: open registration, the `/docs`
  console, and session cookies over plain HTTP. It is the default when
  `DEPLOYMENT_MODE` is unset.
- **`shared_private`** refuses to start while any safety setting is at an
  unsafe value: open registration, `/docs`, unauthenticated legacy routes,
  internal IDs in responses, insecure cookies, rate limiting off, `*` in
  `CORS_ALLOW_ORIGINS`, or a missing or placeholder secret. The error lists
  every problem at once. On a trusted lab network it allows plain-HTTP
  cookies with an explicit opt-in.
- **`hosted_public`** enforces the same settings and always requires secure
  cookies.

The full list of settings is the [Production checklist](production_checklist.md).

| | `local` | `shared_private` | `hosted_public` |
|---|---|---|---|
| Who usually connects | you | your group | anyone, through the ingress |
| Open registration | allowed | off | off |
| Internal IDs in responses | allowed | hidden | hidden |
| Legacy entity routes | open | need login | need login |
| Rate limiting | optional | on | on |
| `/docs` console | allowed | off | off |
| `/redoc` and `/openapi.json` | allowed | your choice (`EXPOSE_API_REFERENCE`) | your choice |
| HTTPS | not needed | recommended | required |
| Review of uploads | optional | optional | expected |
| Backups | your choice | required | required, with a copy on another machine |

## Domains and tunnels

- **On a laptop or a lab network**, use `localhost` or a private address. No
  domain, no tunnel, no public DNS.
- **For a public instance**, use a domain, or a hostname under one, with a
  named Cloudflare Tunnel or a reverse proxy that has a managed certificate.
- **For a quick test**, a temporary tunnel URL (`*.trycloudflare.com`, ngrok)
  is fine. These URLs change, so do not give one to anyone and tear it down
  afterwards.
- **At an institution**, use its domain and its proxy or CDN, which then
  terminates TLS.

Don't forward a router port to the server, and never publish Postgres or
the object store. Only the web UI and the API go through the ingress.

## Air-gapped clusters

TCKDB runs in `local` mode with no network beyond the cluster.

- Login needs nothing external: no identity provider, OAuth or DNS lookup.
- Keep the API on `127.0.0.1` and, if compute nodes must reach it, put a
  proxy on a head-node address in front of it. Don't assume any hosted TCKDB
  is reachable from compute nodes.
- Pull the images on a connected machine and copy them across with
  `docker save` and `docker load`.
- The documentation is in the repository's `docs/` folder and works offline.
- To move data out, export a [contribution bundle](../contribution-bundles/format.md).
  A database dump is not the way to move records between instances.
- Give workflow tools `TCKDB_BASE_URL` and `TCKDB_API_KEY` instead of
  hard-coded URLs, so the same code works against any instance.

## Moving data between instances

Instances never sync. Records move as contribution bundles that you export
from one instance and submit to another:

```text
local instance  --export-->  bundle file  --submit-->  lab or public instance
```

A bundle keeps scientific identity, provenance, artifact hashes, and
literature and software references. It carries public refs and
content-derived identities, never database row IDs. The receiving instance
supports a dry run, validation only, and submit for review. See
[Local to hosted, step by step](../contribution-bundles/manual-local-to-hosted.md).
