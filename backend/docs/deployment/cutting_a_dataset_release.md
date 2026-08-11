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
  *not* the code license. TCKDB's code is MIT; a curated corpus is usually
  published under `CC-BY-4.0`. Decide deliberately;
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
    "data_license": "CC-BY-4.0",
    "code_license": "MIT",
    "citation_text": "TCKDB curated dataset release 2026.07.0. Pieters, C.; Grinberg Dana, A. Technion - Israel Institute of Technology, 2026.",
    "contact": "tckdb-maintainers@example.org",
    "changelog_entry": "First curated release: N species entries, M reaction entries."
  }' | jq
```

Tags are immutable and unique. Use a date-ordered scheme (`YYYY.MM.PATCH`) so
releases sort.

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

Verification re-derives every artifact from the live database and re-renders
the manifest document, comparing digests. Do not deposit anything until this
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
