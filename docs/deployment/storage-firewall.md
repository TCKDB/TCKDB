# Object store firewall

Install this on every Linux host that runs the bundled `seaweedfs` service.
It is one firewall rule, applied at boot by a systemd unit.

## What it closes

Besides the S3 gateway on 9000, SeaweedFS runs a filer (8888), a master
(9333) and a volume server (9340), each with a gRPC port (18888, 19333,
19340; the S3 gateway has 19000), plus admin ports 23646 and 33646. The gRPC
ports take no credentials, because SeaweedFS secures gRPC only with mTLS.
Over gRPC, `weed shell` can delete objects and the bucket's whole collection.

`docker-compose.yml` puts `seaweedfs` on its own `storage` network, which
keeps other containers away from those ports. It does not keep the host away.
Docker bridge addresses are routable from the host's own network namespace,
so any process on the host, and any container started with
`network_mode: host` (such as the `cloudflared` service), can reach every
SeaweedFS port on the container's `storage` address.

The rule closes that route. It is one line at the top of the host's `OUTPUT`
chain:

```
-A OUTPUT -d <storage subnet> -p tcp -m tcp ! --dport 9000 -m conntrack --ctstate NEW \
   -m comment --comment tckdb-548-storage-hostfw -j REJECT --reject-with tcp-reset
```

New TCP connections from the host to the `storage` subnet are reset, except
to port 9000. That port must stay open: `docker-proxy` opens the published S3
forward (`127.0.0.1:9000` to the container's 9000) from the host's namespace,
so blocking 9000 would cut off the S3 API itself. S3 checks a signature on
every request. If the network has an IPv6 subnet, the same rule is added with
`ip6tables`.

Why `OUTPUT` and not `DOCKER-USER`:

- `DOCKER-USER` is reached only from `FORWARD`, which carries traffic routed
  through the host. Traffic the host originates never passes `FORWARD`.
- Traffic between containers on the `storage` network is bridged. It goes
  through `FORWARD`, never `OUTPUT`, so this rule cannot touch the API or the
  worker.

**What stays reachable.** Members of the `storage` network can still use
gRPC: the API container, the worker, and MinIO and the copy tool during a
migration. They already hold the S3 credentials, which can delete every
object, so gRPC gives them nothing new. Only something you would trust with
`S3_SECRET_KEY` may join `storage`. Root on the host can remove the rule;
what the rule stops is unprivileged local processes and host-network
containers.

## Install

Run as root. Install a root-owned copy of the script rather than pointing the
unit at your checkout, so that root never runs a file an unprivileged user
can edit. Copy it again whenever it changes in the repository.

```bash
sudo install -o root -g root -m 0755 backend/scripts/ops/tckdb_storage_hostfw.sh /usr/local/sbin/
sudo install -o root -g root -m 0644 backend/scripts/ops/tckdb-storage-hostfw.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tckdb-storage-hostfw
sudo systemctl status tckdb-storage-hostfw      # active (exited), and the rule it inserted
```

**Which network.** The script takes the first of these that is set:

1. `--network`;
2. `TCKDB_STORAGE_NETWORK`;
3. `${COMPOSE_PROJECT_NAME}_storage`;
4. otherwise, the one network Compose labelled `storage`.

If the host has more than one (another project, a test stack), the script
refuses to guess and the unit fails. Name yours:

```bash
sudo systemctl edit tckdb-storage-hostfw
#   [Service]
#   Environment=TCKDB_STORAGE_NETWORK=tckdb_storage   # your <project>_storage
sudo systemctl restart tckdb-storage-hostfw
```

**Which ports stay open.** Set this with `--allow-ports` or
`TCKDB_STORAGE_ALLOW_PORTS` (comma-separated container ports; the default is
`9000`). Widen it only for a forward you published yourself:

- An API on the host that uses `S3_SEAWEEDFS_MASTER_URL` through
  `127.0.0.1`-published 9333 and 9340 needs `9000,9333,9340`. The gRPC ports
  stay closed.
- While MinIO runs (it joins `storage` too), its console forward on
  `127.0.0.1:9001` may be refused. Add `9001` for the duration.

**When to run it again.** The rule holds the subnet the network had when the
script ran.

- The network is recreated (`docker compose down`, then `up`) and may get a
  new subnet: run `sudo systemctl restart tckdb-storage-hostfw`.
- Docker is restarted: the unit restarts with it, through
  `Requires=docker.service`.
- A firewall manager reloads (`ufw reload`, `firewall-cmd --reload`, an
  `iptables-restore`) and may drop the rule or put its own rules above it:
  restart the unit.

In every case `--check`, below, reports the problem.

## Verify

```bash
sudo /usr/local/sbin/tckdb_storage_hostfw.sh --check
# expect: "present for <network> (<subnet>)" and "OK"; exit 0
```

If the unit sets `TCKDB_STORAGE_NETWORK` or `TCKDB_STORAGE_ALLOW_PORTS`, give
`--check` the same values (`--network`, `--allow-ports`). It compares against
what it is given.

`--check` changes nothing. It exits non-zero if any of these is true:

- the rule is missing for the network's current subnet;
- a stale or duplicate tagged rule is left;
- another `OUTPUT` rule comes before it. `ufw`'s output chains accept new
  connections, so they would win.

Then test the ports from the host, as an ordinary user:

```bash
NET=tckdb_storage          # your <project>_storage
IP=$(docker network inspect "$NET" \
      --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{println}}{{end}}' \
    | awk '/seaweedfs/ {sub(/\/.*/, "", $2); print $2}')
echo "seaweedfs on $NET: ${IP:?not found}"
for p in 8888 9333 9340 18888 19333 19340 19000 23646 33646 9000; do
  if timeout 3 bash -c "exec 3<>/dev/tcp/$IP/$p" 2>/dev/null; then echo "$p open"; else echo "$p closed"; fi
done
# expect: every port "closed" except "9000 open"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9000/   # expect 403 (unsigned)
```

Finally confirm TCKDB still reaches its store: `/api/v1/status` should show
`artifact_storage.healthy: true`.

## Remove

Stopping the unit does not remove the rule. The unit has no `ExecStop` on
purpose: a Docker restart stops and starts it, and the host must not be open
in between.

```bash
sudo systemctl disable --now tckdb-storage-hostfw
sudo /usr/local/sbin/tckdb_storage_hostfw.sh --remove    # every tagged rule, IPv4 and IPv6; needs no Docker
sudo rm /etc/systemd/system/tckdb-storage-hostfw.service /usr/local/sbin/tckdb_storage_hostfw.sh
sudo systemctl daemon-reload
```

## Hosts without iptables

The script is Linux and iptables only. The `iptables-legacy` and
`iptables-nft` backends both work. Elsewhere:

- **Pure nftables, with no iptables tools.** Add the same rule to an `output`
  hook chain and re-add it at boot the same way:
  `nft add rule inet filter output ip daddr <subnet> tcp dport != 9000 ct state new reject with tcp reset`
  (and `ip6 daddr` for an IPv6 subnet). The `inet filter` table and its
  `output` chain must exist.
- **Docker Desktop (macOS, Windows) and rootless Docker.** The bridge lives in
  a VM or a separate network namespace and is normally not routable from the
  host. There is no route to close. Confirm it with the port loop above.
- **Docker installed under another unit name** (for example the snap's
  `snap.docker.dockerd.service`). Change `After=` and `Requires=` in the unit
  to match.
- **SeaweedFS run natively, not in Docker.** This rule does not apply. Bind it
  to loopback instead, as [Without Docker](native-advanced.md#object-store)
  describes.

Whichever way you close it, the port loop is the test. Every port except 9000
must be closed from the host.
