# Monitoring and alerting

How to know TCKDB is broken without looking. Written to be re-runnable on a
fresh host by someone who has not done this before.

## The idea, in one page

There are three separate jobs, and it is worth keeping them separate in your
head because most confusion about monitoring comes from mixing them up. Before
them sits one precondition that all three inherit.

**0. The endpoint has to cover every hard dependency.** This comes first
because it is the one that bites. A health check that omits a dependency is
not merely incomplete — it converts an outage into a confident all-clear, and
everything downstream of it (the checker, the dead man's switch, the deploy
verification) inherits the lie. See "What a missing component costs" below;
it is not hypothetical here.

**1. The system reports on itself.** An endpoint answers "is anything wrong,
and what". TCKDB has three, and they are not redundant:

| Endpoint | Question | Consumer |
|---|---|---|
| `/api/v1/health` | is the process alive? | a restarter |
| `/api/v1/readyz` | can it serve traffic? | a load balancer |
| `/api/v1/status` | is anything wrong, and what? | a human, and the checker below |

The split matters. *Liveness* failing means restart me. *Readiness* failing
means stop sending me traffic but do **not** restart me — I may be waiting on
the database, and restarting would only lose in-flight work. `/status` is the
rich one, and it deliberately returns **200 even when degraded**: if it
returned 500, a checker could not tell "the site is down" from "the site is up
and telling me the worker died", and those are different problems.

Because they answer different questions, they cover different things. Artifact
storage appears in `/status` but deliberately *not* in `/readyz`: with the
object store down, every read and every query still works, so pulling the
instance out of rotation would turn a partial outage into a total one. "Is
anything wrong" and "should you route to me" are not the same question.

**2. Something polls that endpoint and decides whether to bother you.**
`scripts/ops/tckdb_alert_check.sh`, run every 5 minutes by a systemd timer. It
notifies **only when the answer changes** — broken *and* recovered. An alert
that fires every five minutes is one you learn to ignore, and an alert you
ignore is worse than none.

**3. Something notices when the whole host disappears.** This is the part
people get wrong, including in the first version of this setup. The checker in
step 2 runs *on the Pi*. If the Pi loses power, the checker dies with it and
you hear **nothing** — the silence is indistinguishable from everything being
fine.

The rule: **your alerting must not share a failure domain with the thing it
watches.** The fix is a *dead man's switch* — the Pi pings an external service
on a schedule, and that service alerts when the ping *stops*. Absence becomes
the signal. Setup is in the last section.

**4. Something notices when the checker dies but the host does not.** The
narrower and more likely version of (3), and the one that hid for longest: the
host is fine, the API is fine, and the *checker* is gone. The timer was never
enabled, the unit failed on a missing `EnvironmentFile`, `ExecStart` points at
a path the repo has since moved. Nothing is pushed, nothing complains, and the
absence of alerts reads as good news. Two mechanisms cover it, in two failure
domains: `TCKDB_DEADMAN_URL` (external, also covers the host dying) and
`OnFailure=tckdb-alert-failed.service` (local, catches the cases where the
script never gets far enough to ping anything). Neither alone is enough.

## What `/status` reports

```json
{
  "status": "ok",
  "degraded": [],
  "components": {
    "database": {"healthy": true, "alembic_revision": "f9b2e6c4a1d7", "reason": null},
    "worker": {
      "inline": true, "thread_alive": true,
      "queued": 0, "oldest_queued_age_seconds": null,
      "queue_stalled": false, "healthy": true, "reason": null
    },
    "artifact_storage": {
      "endpoint": "http://minio:9000", "bucket": "tckdb-artifacts",
      "healthy": true, "reachable": true, "reason": null
    }
  }
}
```

`status` is `"ok"` only when `degraded` is empty; both are derived from the
same set of unhealthy components, so anything consuming this should check
both rather than rely on that derivation staying true. All three consumers
now do: `tckdb_deploy.sh`'s verification loop, `tckdb_alert_check.sh` (on both
its `jq` and its `jq`-free parse paths), and `.github/workflows/uptime-check.yml`.
A `status: "ok"` accompanied by a non-empty `degraded` is reported as its own
kind of fault — the endpoint contradicting itself has a different fix from a
component being down.

Reading one of two fields is exactly how monitoring comes to vouch against an
outage, which has happened here once already. It is cheap to read both, and
the cost of not doing so is paid in a way you cannot see.

The worker block carries two independent signals because neither is sufficient:

- **`thread_alive`** — the worker runs as a thread inside the API process
  (`TCKDB_INLINE_WORKER=true`, appropriate at this scale). If that thread dies,
  the process stays up, systemd sees a healthy service, the API keeps answering
  200, and `/jobs/*` keeps accepting uploads that will never be processed. This
  is the silent failure the whole endpoint exists for.
- **`queued` / `oldest_queued_age_seconds`** — if a job has sat unclaimed for
  more than 5 minutes, nothing is picking up work, whatever shape the worker
  is deployed in. This catches a dead *separate-process* worker too, but only
  says anything when there is work to do.

The obvious signal — a worker heartbeat — does not work here:
`upload_job.heartbeat_at` is written only *while processing a job*, so an idle
worker and a dead one look identical. Hence checking the thread directly.

The **`artifact_storage`** block answers "can an upload's files actually be
stored". Artifacts (ESS output logs, checkpoints) go to an S3-compatible object
store, not to PostgreSQL, so it fails entirely independently of the database
and the worker — which is exactly why it has to be listed here.

- **`endpoint` / `bucket`** — *what the API tried to reach*, not just whether
  it worked. This is the field that turns a confusing outage into a five-second
  diagnosis. The URL is reduced to `scheme://host:port` before it is reported,
  so a credential accidentally embedded in `S3_ENDPOINT_URL` cannot leak from
  this public endpoint.
- **`reachable`** — `false` means no HTTP response came back at all: wrong
  address, service down, DNS or TLS failure. `true` with `healthy: false` means
  the store answered and refused: the bucket does not exist, or the credentials
  are rejected. Different symptoms, different fixes, so they are never
  collapsed into one "storage is broken". (`database` splits *unreachable* from
  *schema not initialised* for the same reason.)
- The probe is a **`head_bucket`**, not a write — no load, no stray objects,
  no dependence on write quota. It is bounded by a short botocore timeout
  *and* a hard wall-clock deadline enforced outside botocore, because
  botocore's timeouts do not cover every way a call can stall (DNS is the
  usual one). A dead object store degrades `/status`; it must never hang it.
  A hung `/status` reads to a checker as "host down", which is the wrong page
  of the runbook.

### What a missing component costs

On 2026-08-05 the containerised API ran with
`S3_ENDPOINT_URL=http://127.0.0.1:9000`, inherited from the earlier deployment
where the API ran on the host under systemd. Inside a container that address is
the *container's own* loopback, where nothing is listening.

Every artifact-bearing upload returned 503. Meanwhile `/status` — which then
covered only the database and the worker — reported fully healthy, so the
5-minute checker never fired, the dead man's switch kept receiving its pings
(the host was fine; only a dependency was not), and the deploy script's
post-deploy verification, which waited for `status:ok`, passed throughout. The
monitoring did not miss the outage so much as vouch against it.

Two lessons, both now enforced in code:

1. Anything that can fail *alone and silently* while the rest stays green must
   appear in `/status`. When you add a hard dependency to the API, add it here
   in the same change.
2. Report **what was reached for**, not only the verdict. Every value in that
   deployment's configuration was individually valid; the only way to see the
   fault was to see the address in use.

### The same probe, at boot

`/status` answers on demand. That fault was answerable from the first second
of the process's life, and no one asked for hours. So the API now probes the
object store once during startup and writes a single line into the container
log, naming the endpoint and bucket it tried:

```text
startup: ARTIFACT STORAGE UNAVAILABLE - endpoint=http://127.0.0.1:9000 bucket=tckdb-artifacts reachable=False reason=...
```

That is where the deploy script already sends you (`docker logs tckdb-api |
tail -50`), so the diagnosis is waiting before anyone looks for it.

It **never fails startup.** With the object store down, reads, queries and
uploads without attached files all still work; exiting would replace a partial
outage with a total one — the same reasoning that keeps artifact storage out of
`/readyz`. It reuses `/status`'s probe, and therefore its wall-clock deadline,
so an unreachable endpoint costs a bounded few seconds of boot and can never
hang it. Set `TCKDB_STARTUP_PROBES=false` to opt out of this and the database
encoding probe together; the test suite does, because it builds the app
hundreds of times.

## Setting it up on a fresh host

**1. Pick a topic name.** ntfy topics are public to anyone who knows the name,
so treat it like a password:

```bash
echo "tckdb-$(head -c 9 /dev/urandom | base32 | tr '[:upper:]' '[:lower:]')"
```

**2. Install the ntfy app** (Android/iOS/desktop) and subscribe to that topic.

**3. Write the environment file** — kept separate from the unit because unit
files are world-readable:

```bash
install -m 600 /dev/null ~/.config/tckdb-alert.env
cat > ~/.config/tckdb-alert.env <<'EOF'
TCKDB_NTFY_TOPIC=tckdb-your-random-topic-here
TCKDB_STATUS_URL=https://tckdb.homecalvin.com/api/v1/status
EOF
```

**4. Install the units and start the timer:**

```bash
sudo cp backend/scripts/ops/tckdb-alert.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tckdb-alert.timer
```

**5. Verify it works, rather than assuming:**

```bash
systemctl list-timers tckdb-alert.timer      # when does it next run?
sudo systemctl start tckdb-alert.service     # run it once, now
journalctl -u tckdb-alert -n 20 --no-pager   # what did it decide?
```

**6. Prove the alert path end to end.** A monitoring system you have never seen
fire is a monitoring system you do not have:

```bash
curl -H "Title: TCKDB test" -d "If you see this, alerting works." \
  ntfy.sh/tckdb-your-random-topic-here
```

Then break something on purpose — `sudo systemctl stop tckdb-api` — run the
check, confirm the push arrives, start it again, confirm the recovery push
arrives. Ten minutes, and it is the only way to know.

## The dead man's switch (covers the checker dying, and the host with it)

The checker above cannot report its own death. Close that with a free external
service — [healthchecks.io](https://healthchecks.io) is the usual choice —
which expects a ping on a schedule and alerts when one does not arrive.

1. Create a check with a period of 10 minutes and a grace of 5.
2. Copy its ping URL.
3. Add it to `~/.config/tckdb-alert.env` as `TCKDB_DEADMAN_URL=...`. The
   checker pings it itself; there is nothing to append and no second timer.
4. Point healthchecks.io's notification at the **same ntfy topic**, so both
   classes of alarm land in one place.

**The ping means "the checker ran", not "TCKDB is well",** and it is sent on
every completed run including a degraded one. That distinction is the whole
design. If the ping were conditional on a healthy verdict, an object-store
outage would stop the heartbeat, healthchecks.io would page *the host is gone*,
and you would go looking for a dead Raspberry Pi while the Pi was pushing an
accurate description of the real fault to your phone — the wrong page of the
runbook, produced by your own monitoring. The two channels answer two
questions and must never be collapsed into one.

The ping is deliberately **not** sent when the script aborts before reaching a
verdict — an unset `TCKDB_NTFY_TOPIC`, say. That is a dead checker, and its
silence is the only signal it can send. Forging a heartbeat there would be
worse than having none.

If `TCKDB_DEADMAN_URL` is unset the checker pushes one notice saying so, on its
first run, and then never mentions it again. An unmonitored monitor is worth
exactly one interruption.

### When the checker cannot even start

`tckdb-alert.service` carries `OnFailure=tckdb-alert-failed.service`, so a unit
that fails — moved `ExecStart` path, unreadable `EnvironmentFile`, exit 2 from
a missing topic — pushes *"TCKDB health checker FAILED"* rather than leaving a
red unit nobody looks at. Install it alongside the others:

```bash
sudo cp backend/scripts/ops/tckdb-alert-failed.service /etc/systemd/system/
sudo systemctl daemon-reload
```

It runs on the same host, so it cannot cover the host going away; that is what
the dead man's switch is for. Between them: the Pi tells you when a component
breaks, systemd tells you when the checker breaks, and healthchecks.io tells
you when the Pi stops talking at all. No one of them depends on another
surviving.

Prove it, rather than assuming — the same ten minutes that is the only way to
know anything about monitoring:

```bash
sudo systemctl stop tckdb-alert.timer     # then wait past the deadman period
# expect: a healthchecks.io alert, and no ntfy traffic at all
sudo systemctl start tckdb-alert.timer
```

The behaviour above is covered by `backend/tests/ops/test_alert_check_liveness.py`,
which runs the real script as a subprocess against a fake host and asserts what
reaches the outside world on each broken path — including that a checker which
refuses to start sends nothing.

## Redeploying elsewhere

Everything above is three files in the repo — the script and the two units —
plus one environment file holding a topic name. Nothing is baked into the host.
On a new machine: copy, set `TCKDB_STATUS_URL` to the new address, enable the
timer, send the test push. If you keep using the same ntfy topic, alerts from
the old and new deployments arrive in the same place, which is usually what you
want during a migration.
