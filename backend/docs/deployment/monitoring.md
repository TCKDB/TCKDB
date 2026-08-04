# Monitoring and alerting

How to know TCKDB is broken without looking. Written to be re-runnable on a
fresh host by someone who has not done this before.

## The idea, in one page

There are three separate jobs, and it is worth keeping them separate in your
head because most confusion about monitoring comes from mixing them up.

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
    }
  }
}
```

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

## The dead man's switch (covers total host death)

The checker above cannot report its own host dying. Close that with a free
external service — [healthchecks.io](https://healthchecks.io) is the usual
choice — which expects a ping on a schedule and alerts when one does not
arrive.

1. Create a check with a period of 10 minutes and a grace of 5.
2. Copy its ping URL.
3. Add it to `~/.config/tckdb-alert.env` as `TCKDB_DEADMAN_URL=...` and append
   a curl to that URL at the end of a healthy check run, or simply add a
   second systemd timer that pings it.
4. Point healthchecks.io's notification at the **same ntfy topic**, so both
   classes of alarm land in one place.

Now: the Pi tells you when a component breaks, and healthchecks.io tells you
when the Pi stops talking at all. Neither depends on the other surviving.

## Redeploying elsewhere

Everything above is three files in the repo — the script and the two units —
plus one environment file holding a topic name. Nothing is baked into the host.
On a new machine: copy, set `TCKDB_STATUS_URL` to the new address, enable the
timer, send the test push. If you keep using the same ntfy topic, alerts from
the old and new deployments arrive in the same place, which is usually what you
want during a migration.
