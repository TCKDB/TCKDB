# Runbook — cutting a citable dataset release

Audience: a TCKDB curator or operator publishing a curated scientific dataset,
typically alongside a paper.

Design rationale is in
[`../specs/dataset_release_and_profiles.md`](../specs/dataset_release_and_profiles.md).
This file is the procedure.

> **A dataset release is not a backup.** It ships selected values plus the
> candidates and review history behind them. It cannot restore a database.
> For that, use `scripts/tckdb_archive.py` (`tckdb.archive.v1`) and
> [`migrations.md`](migrations.md).

---

## 0. Before you start

You need:

- a curator or admin API key;
- agreement on the **data license** (the scientific corpus) — this is normally
  *not* the code license. TCKDB's code is MIT; the corpus is published under
  `CC-BY-4.0`, which is now the house default: omit `data_license` and
  `code_license` from the create call and you get `CC-BY-4.0` + `MIT`. Send
  them only if this deployment publishes under different terms, and see
  [`LICENSE-DATA`](../../../LICENSE-DATA) for what the data license covers.
  **The default expresses what the operator is entitled to license.** On a
  deployment with more than one depositor, do not cut a release until the
  license has been agreed with each depositor at deposit time — a
  configuration default is not their consent;
- a citation string;
- a maintainer contact address that will still work in five years;
- records actually in `approved` review state. This is **enforced**: a
  selection naming a record below `approved` is refused (422
  `record_not_approved`), and publishing re-checks in case a record was
  demoted in between. An unapproved product row is not covered by the
  accepted-science immutability trigger, so a release that recommended one
  could have its own recommended value edited afterwards.

Check what the API thinks it is running:

```bash
curl -s https://<host>/api/v1/readyz | jq
# {"status":"ready","database":"ok","alembic_revision":"..."}
```

That revision is bound into the manifest. If it is not the revision you intend
to publish under, stop and resolve the drift first.

---

## 1. Register the curation policy version

A release cites a **named, versioned** rubric. Registering the same
`(name, version)` again with different content is refused — a policy version
that a published release cites must never change.

```bash
curl -sX POST https://<host>/api/v1/releases/policies \
  -H "X-API-Key: $TCKDB_API_KEY" -H 'Content-Type: application/json' \
  -d '{
    "name": "tckdb-benchmark",
    "version": "1.0",
    "description": "Prefer the highest-level composite single point with a converged frequency calculation at the same or a compatible level; require an approved review and a named level of theory; break ties by the most recent approval.",
    "criteria": {
      "requires_review_status": "approved",
      "requires_level_of_theory": true,
      "requires_converged_frequency": true,
      "tie_break": ["method_rank", "first_approved_at", "record_id"]
    }
  }' | jq
```

Write the `description` for a referee, not for yourself. It is published
verbatim in every manifest that cites the policy.

---

## 2. Open a draft release

```bash
curl -sX POST https://<host>/api/v1/releases \
  -H "X-API-Key: $TCKDB_API_KEY" -H 'Content-Type: application/json' \
  -d '{
    "tag": "2026.07.0",
    "title": "TCKDB curated thermochemistry and kinetics, July 2026",
    "curation_policy_name": "tckdb-benchmark",
    "curation_policy_version": "1.0",
    "citation_text": "TCKDB curated dataset release 2026.07.0. Pieters, C.; Grinberg Dana, A. Technion - Israel Institute of Technology, 2026.",
    "contact": "tckdb-maintainers@example.org",
    "changelog_entry": "First curated release: N species entries, M reaction entries."
  }' | jq
```

Tags are immutable and unique. Use a date-ordered scheme (`YYYY.MM.PATCH`) so
releases sort.

`data_license` and `code_license` are omitted above on purpose: the release
comes out as `CC-BY-4.0` + `MIT` without them. Pass either one to publish
under different terms — what you pass is stored verbatim and is what the
manifest and every reader are told. An empty string is refused, because "I did
not say" and "there is no license" are different claims.

There is no `doi` field here. See step 6.

---

## 3. Append selections

One call per curated decision. Records are addressed by **public ref**; the
subject is derived from the record, so you cannot attach a thermo record to the
wrong species entry.

```bash
curl -sX POST https://<host>/api/v1/releases/2026.07.0/selections \
  -H "X-API-Key: $TCKDB_API_KEY" -H 'Content-Type: application/json' \
  -d '{
    "record_ref": "thm_01h9x...",
    "rationale": "CCSD(T)-F12/cc-pVTZ-F12 composite single point on an M06-2X/def2-TZVP optimised geometry; all frequencies real; AEC applied. Preferred over the B3LYP candidate, whose 298 K entropy disagrees with the ATcT value by 1.8 J/mol/K."
  }' | jq
```

The `rationale` is published. Write something a referee could disagree with;
"best available" is not a rationale.

**Changing your mind — supersede, never edit.** There is no `PATCH`:

```bash
curl -sX POST \
  https://<host>/api/v1/releases/2026.07.0/selections/rsel_.../supersede \
  -H "X-API-Key: $TCKDB_API_KEY" -H 'Content-Type: application/json' \
  -d '{"record_ref": "thm_02k4y...", "rationale": "New W1X-2 result supersedes the earlier composite; the earlier value stays in the ledger."}' | jq
```

**Recommending nothing** is a legitimate position:

```bash
curl -sX POST \
  https://<host>/api/v1/releases/2026.07.0/selections/rsel_.../withdraw \
  -H "X-API-Key: $TCKDB_API_KEY" -H 'Content-Type: application/json' \
  -d '{"reason": "Both candidates disagree with the shock-tube measurement beyond stated uncertainty; the release makes no recommendation until this is resolved."}' | jq
```

Review the ledger before publishing:

```bash
curl -s "https://<host>/api/v1/scientific/releases/2026.07.0/selections?limit=200" | jq '.records[] | {selection_ref, action, stands, record_ref, rationale}'
```

---

## 3b. Verify custody of the evidence the release will cite

Run this **before** publishing. A release is the moment TCKDB turns a set of
records into a citable claim, so it is the moment "we still hold the evidence
behind these" has to be true — and it is the trigger ADR 0014 chose instead of
a cron, because the cost of re-reading objects should be paid against what
someone will actually cite rather than against total stored volume.

```bash
conda run -n tckdb_env python backend/scripts/ops/verify_artifact_integrity.py \
  --release <release_public_ref>
```

**Gate on "not zero", and record the `verified=` count in the release notes.**
There are three ways this stops being a clean gate and they need different
responses:

| Exit | Meaning | What to do |
|---|---|---|
| `0` | Every digest in scope was read back and hashed correctly. | Publish. |
| `1` | At least one break was recorded. | Investigate before publishing. |
| `2` | **Nothing was verified.** The scope matched no digests, the release cites no calculations, the object store did not answer, or the invocation itself was refused. | Fix the invocation, the scope or the store, then re-run. Do not publish on the strength of this. |
| `3` | **Something was repaired.** A held object a committed row references was put back at its content-addressed key and re-read. | Nothing is wrong with the evidence, but the orphan reclaim lost its documented race with `store_artifact` dedup against real data. Read the `reclaim_restore` observations before the next `--reclaim-orphans` run. |

Exit `3` only appears on invocations that touch the reclaim hold
(`--orphans`, `--reclaim-orphans`, `--purge-hold-days`), so a plain
`--release` gate will not see it.

`breaks=0` is equally true of a sweep that read four hundred objects and one
that read none, so the number that says whether this run is evidence of
anything is `verified=`. The summary line prints it first, beside `unchecked=`
and `in_scope=`.

Exit `2` on an empty scope is deliberate and can be overridden with
`--allow-empty` when the emptiness is expected — a release whose cited
calculations genuinely retained no artifacts, or a fresh deployment. Saying so
in the invocation is the point: an empty scope and a mistyped ref are otherwise
the same output.

Every break is written to `artifact_integrity_event`, which hard-fails the
owning calculation at read time; investigate before publishing rather than
shipping a release whose evidence TCKDB cannot produce. The row carries a
`public_ref` (`aie_…`) and is readable at
`GET /scientific/artifacts/{sha256}/integrity` (curator or admin). See
`docs/adr/0014-custody-of-stored-evidence-is-recorded-not-logged.md` for how to
read it and tell the three causes apart.

Re-check one digest by hand with `--sha256 <digest>`. This works even for a
digest no `calculation_artifact` row references — the `store_artifact` dedup
path records breaks against objects whose referencing row was refused, and
those are the ones most likely to be handed to an operator to look at.

`--release` covers what the release actually rests on: for every selection that
still stands, the calculations that record cites through its
`*_source_calculation` table — the same list the published release artifact
prints as cited provenance — plus everything those calculations depend on,
transitively. A release cannot select a calculation directly (the selectable
record types are the six product/entry types), so that traversal is the whole
of the scope; the command exits `2` with `cites no calculations` rather
than reporting a clean sweep over an empty set.

Objects outside any release are still covered by nothing in particular. Pair
this with `--all --sample 0.02` on a schedule if you want a detection-time
distribution between releases.

---

## 4. Publish — this freezes the manifest

**Publishing is irreversible in practice.** After this, selections can no
longer be appended and the manifest checksums are frozen.

```bash
curl -sX POST https://<host>/api/v1/releases/2026.07.0/publish \
  -H "X-API-Key: $TCKDB_API_KEY" | jq
```

---

## 5. Verify, then download the artifacts

Verification **re-hashes the frozen bytes**: the stored artifact rows and the
stored manifest document are hashed again and compared with the digests
recorded at publication (`verify_release` in
`app/services/release/manifest.py`). It reads nothing from the live corpus,
so a new upload or a review advancing can never make it fail. The separate,
**non-blocking** `live_divergence` report is what compares the live database
with the release (see below). Do not deposit anything until verification
reports `verified: true`.

```bash
curl -s https://<host>/api/v1/scientific/releases/2026.07.0/manifest \
  | jq '{verified: .verification.verified,
         problems: .verification.problems,
         sha: .manifest.content_sha256,
         versions: .manifest.versions,
         artifacts: [.manifest.artifacts[] | {path, sha256, byte_count, record_count}]}'
```

Download and independently check each file:

```bash
mkdir -p tckdb-2026.07.0 && cd tckdb-2026.07.0
curl -s https://<host>/api/v1/scientific/releases/2026.07.0/manifest \
  | jq '.manifest.document' > manifest.json

for path in selected_records.ndjson candidate_records.ndjson \
            review_history.ndjson selection_ledger.ndjson; do
  curl -sO "https://<host>/api/v1/scientific/releases/2026.07.0/artifacts/$path"
done

# Compare against the manifest, not against a hash the server just told you.
jq -r '.artifacts[] | "\(.sha256)  \(.path)"' manifest.json | sha256sum -c -
```

Also confirm the manifest digest itself, which is a SHA-256 over the canonical
JSON (`sort_keys`, `separators=(",",":")`, UTF-8) of `manifest.json`:

```bash
python3 -c "
import hashlib, json, sys
doc = json.load(open('manifest.json'))
body = json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
print(hashlib.sha256(body.encode()).hexdigest())
"
# must equal .manifest.content_sha256 from the API
```

The artifacts you download are the **frozen bytes**, stored at publication.
They do not change when the corpus does, so this step gives the same files
whether you run it a minute or a year after publishing.

`verification` answers "is the frozen release intact?" — it re-hashes the
stored bytes against the recorded digests and is independent of the live
database. It should always be `true`; `false` means the stored rows were
tampered with, so restore from backup rather than depositing.

`live_divergence` is a **different, non-fatal** report: it says how far the
database has moved since publication.

```bash
curl -s https://<host>/api/v1/scientific/releases/2026.07.0/manifest \
  | jq '.live_divergence'
```

`diverged: true` is the normal steady state of a live instance — new uploads
arrive and review advances, and the release deliberately does not move with
them. It is **not** a reason to withhold a deposit, and it does not affect the
checksums. If you want the newer state published, cut the next release.

---

## 5b. Build the publication deposit

The curl procedure above gives you the release. A **deposit**
(`tckdb.deposit.v1`) is the directory a paper is backed by: the release, the
`tckdb.archive.v1` evidence archive written in the same database session,
the exact source commit and its lockfiles, the scripts that generate every
manuscript number, those scripts' expected outputs, a `REPRODUCE.md`
protocol and an `ACCOUNTS.md` privacy statement -- all bound by SHA-256 in
one `MANIFEST.json`. Build it **on the host that holds the database**, from a
clean checkout of the exact tagged commit that is deployed:

```bash
git status --porcelain --untracked-files=no   # must print nothing (untracked files are only warned about)
git describe --tags --exact-match             # must print the paper tag
export DB_USER=... DB_PASSWORD=... DB_HOST=... DB_PORT=... DB_NAME=...
export S3_ENDPOINT_URL=... S3_ACCESS_KEY=... S3_SECRET_KEY=... S3_BUCKET=... S3_REGION=...

conda run -n tckdb_env python backend/scripts/ops/build_publication_deposit.py build \
  --release 2026.07.0 \
  --output /srv/deposits/tckdb-2026.07.0 \
  --author-account alice --author-account bob
```

The build reads the release from the database (never over HTTP) and refuses,
with a distinct exit code, when:

| exit | refusal |
| --- | --- |
| 2 | the release is missing, unfrozen, withdrawn, or `verify_release` reports a problem; or the `release_artifact` rows inside the archive do not hash to the emitted release files |
| 3 | a tracked path is modified, staged, deleted or renamed (untracked files are printed as a warning, never refused), `HEAD` carries no tag, a package version resolves to `unknown`, or a source pin (`backend/environment.yml`, `backend/uv.lock`, `backend/Dockerfile`, `CITATION.cff`, `LICENSE`, `LICENSE-DATA`) is missing |
| 4 | the database's `alembic_version` is not the checkout's Alembic script head |
| 5 | any `app_user` row, or any actor reference (`created_by`, `selected_by`, `reviewed_by`, and every other foreign key onto `app_user`), resolves outside the `--author-account` list -- the message names usernames only |
| 6 | the output directory already has content |

There is no redaction mode: the archive is byte-exact, so the allowlist is
the privacy control, and `ACCOUNTS.md` states the residual risk that ESS
output files embed the authors' cluster paths.

Then verify it, offline and against the database:

```bash
conda run -n tckdb_env python backend/scripts/ops/build_publication_deposit.py verify /srv/deposits/tckdb-2026.07.0
conda run -n tckdb_env python backend/scripts/ops/build_publication_deposit.py verify /srv/deposits/tckdb-2026.07.0 --db
conda run -n tckdb_env python backend/scripts/tckdb_archive.py verify /srv/deposits/tckdb-2026.07.0/archive/2026.07.0.archive.tar
```

`REPRODUCE.md` inside the deposit is the protocol a reader follows from the
deposit alone: check out the pinned commit, migrate an empty database,
restore the archive, re-run the generators, byte-diff against
`expected_outputs/`, and run the two verifiers. Run it yourself once on a
throwaway database before minting a DOI for the deposit directory. The
deposit's `MANIFEST.json` records the git commit from `git rev-parse HEAD`
at build time; the release manifest itself does not yet carry a commit.

---

## 6. Mint the DOI — the manual step

**TCKDB does not mint DOIs.** A DOI is not retractable; the machinery is built
so that depositing is a deliberate human act at the moment a paper tag is cut,
not a side effect of publishing a release.

When you are ready:

1. Create a Zenodo deposition (or your institution's repository) for the
   directory assembled in step 5 — `manifest.json` plus the four NDJSON files.
2. Set the deposition metadata to match the release exactly:
   - title → `release.title`
   - version → the release `tag`
   - license → `release.data_license` (the **data** license, not MIT)
   - description → `release.description` + `release.changelog_entry`, and a
     line stating the manifest `content_sha256`
   - creators → the curators named in `selection_ledger.ndjson`
   - related identifier → the software repository / its own citation
3. Publish the deposition and copy the resulting DOI.
4. Record it against the release:

```bash
curl -sX POST https://<host>/api/v1/releases/2026.07.0/doi \
  -H "X-API-Key: $TCKDB_API_KEY" -H 'Content-Type: application/json' \
  -d '{"doi": "10.5281/zenodo.XXXXXXX"}' | jq
```

Recording a *different* DOI later is refused: that would silently repoint a
citation. If a deposit was wrong, withdraw the release (step 7) and cut a new
one.

> Attaching the DOI annotates the `dataset_release` row and does **not** touch
> the frozen manifest: the document is served from a snapshot taken at
> publication and reports `release.doi_at_publication` (which is `null`, since
> no DOI existed then). `verification` stays `true`. The live DOI is visible on
> `GET /api/v1/scientific/releases/{tag}`, and `live_divergence` notes that the
> release metadata was annotated after publication.
>
> This was previously not the case — recording the DOI re-rendered the document
> from the live row and broke the digest permanently, which meant every
> genuinely deposited release reported `verified: false`.

---

## 7. Withdrawing a release

Retract, do not delete. The row and its manifest stay readable so an
outstanding citation resolves to an explicit "withdrawn" rather than a 404.

```bash
curl -sX POST https://<host>/api/v1/releases/2026.07.0/withdraw \
  -H "X-API-Key: $TCKDB_API_KEY" -H 'Content-Type: application/json' \
  -d '{"reason": "Systematic error in the applied AEC scheme affects 12 species; superseded by 2026.08.0."}' | jq
```

If a DOI was minted, also mark the Zenodo record as retracted/superseded there
— TCKDB cannot do that for you.

Withdrawing does **not** make the release droppable. The migration's downgrade
guard refuses to run while any release is `published` *or* `withdrawn`, because
a withdrawn release is exactly the case where an outstanding citation must
still resolve — to an explicit retraction rather than a 404.

---

## 8. Citing a release

In a paper:

> Thermochemical and kinetic parameters were taken from TCKDB dataset release
> **2026.07.0** (manifest SHA-256 `abc123…`, DOI `10.5281/zenodo.XXXXXXX`),
> curated under policy `tckdb-benchmark` v1.0. The full candidate set and
> review history behind every selected value are distributed with the release.

Cite the **software** separately using [`CITATION.cff`](../../../CITATION.cff).

## 9. Reproducing someone else's citation

```bash
curl -s https://<host>/api/v1/scientific/releases/<tag>/manifest | jq '.manifest.versions'
```

Gives the Alembic revision and package versions the numbers were produced
under. Then fetch `selected_records.ndjson` for the values, and
`candidate_records.ndjson` + `review_history.ndjson` to see everything that was
*not* selected and why the selected record was trusted. Disagreeing with a
TCKDB recommendation should require no privileged access.
