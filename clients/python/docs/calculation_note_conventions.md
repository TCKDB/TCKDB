# `Calculation.note` conventions

Status: **conventions for producer adapters.** No backend changes
in this document. Audience: workflow-tool adapter authors and
producer-side code using `tckdb_client.builders`.

Companion to [`builder_api_mvp.md`](builder_api_mvp.md),
[`conformer_semantic_boundary.md`](conformer_semantic_boundary.md),
and [`parser_validation_boundary.md`](parser_validation_boundary.md).
This document is the producer-facing answer to *"what should I put
in `Calculation.note=…`?"* before the backend grows a wire-level
note field.

---

## 1. Purpose

`Calculation.note` is a short, human-readable annotation attached
to a `Calculation` builder object. It exists so producers and
workflow-tool adapters can preserve the *one-line "why"* of a
specific calculation without inventing a custom side-channel or
reaching into private builder state.

`Calculation.note` is **not** a replacement for any of these:

- **`artifacts`** — input decks, output logs, checkpoint files,
  scan tables; the durable bytes story.
- **Structured provenance** — `source_calculations`, `depends_on`,
  `level_of_theory`, `software_release`, `workflow_tool_release`,
  `literature`. Everything the backend can index, dedupe, or
  validate goes here.
- **`thermo.source_calculations` / `statmech.source_calculations` /
  `kinetics.source_calculations`** — these are the load-bearing
  scientific provenance edges; `note` is text *about* a calc, not
  a link.
- **The upload-level `note` fields** on `Conformer`, `Thermo`,
  `Statmech`, `TransitionState`, `Kinetics`, etc. Those *are*
  emitted on the wire today and have their own conventions.

If a fact about a calculation can be expressed in any of the above,
**use the structured field**. Reach for `Calculation.note` only
when the information is genuinely free-text and short.

---

## 2. Current emission behaviour

State of `Calculation.note` at the time of writing:

- `Calculation.note` is **builder-local** in the current client.
- It is **not emitted** by `ComputedSpeciesUpload.to_payload()`.
- It is **not emitted** by `ComputedReactionUpload.to_payload()`.
- It does **not appear** in `upload.summary().to_dict()` or
  `upload.summary().to_text()` — annotations are free text and the
  summary surface deliberately excludes free-text fields (see
  [`builder_summary_design.md`](builder_summary_design.md) §4).

The value is preserved on the builder object after construction.
Producers and adapters can read it back via the public iteration
helpers:

```python
for calc in upload.iter_calculations():
    if calc.note:
        print(f"{calc.label}: {calc.note}")
```

This is useful for local CLI / log views, but the value never
crosses the wire today. Backend schema support for a per-calc note
field **may** be added later (see §7), but no producer should
assume it. Treat `Calculation.note` as durable for *your own*
post-processing and audit, not for downstream TCKDB consumers.

---

## 3. Good uses

Short free-text annotations whose absence wouldn't break the upload
but whose presence helps a future reader understand the producer's
intent. Examples:

- *"Single-point refinement on the optimised geometry."*
- *"Frequency calculation performed on the selected optimised
  geometry; one imaginary mode at the saddle."*
- *"Reused from a workflow run on 2026-03-12 — same geometry, same
  level of theory."*
- *"Workflow chose this LoT because the cheaper composite gave
  unphysical Ea ordering for this family."*
- *"Lowest-energy converged structure from a CREST conformer scan;
  search history retained as artifacts."*
- *"Curator note: this calculation supersedes the earlier upload
  of the same species."*

What ties these together:

- One short sentence (or two).
- Says *why*, not *what* (the structured fields say *what*).
- Survives summarisation — a future reader scanning a hundred calcs
  benefits from the line, not a paragraph.
- Doesn't repeat information already in a structured field.

---

## 4. Bad uses

`Calculation.note` is **not** the right place for any of these:

- **Full log files / multi-page output text.** That's what
  artifacts exist for.
- **Large text blobs.** A note is one line, not a manifest.
- **Candidate-conformer search histories** — see §6.
- **Raw input / output deck contents.** Upload them as artifacts.
- **Information that is already represented in a structured field.**
  If you find yourself writing `"Run on Gaussian 16 C.01"`, that
  belongs on `SoftwareRelease`. If you find yourself writing
  `"depends on calc X"`, that belongs on `depends_on`. If you find
  yourself writing the LoT, that belongs on `LevelOfTheory`. Notes
  about provenance that *are* in structured fields are noise; notes
  about provenance that *aren't* in structured fields are the place
  to ask whether a structured field is missing.
- **Sensitive or machine-local paths** (e.g. `/home/alice/runs/…`,
  cluster paths, internal hostnames). Notes are read by humans and
  may be preserved in audit trails; treat them as public-facing.
- **Workflow scratchpad / debug text** — convergence-tweak diaries,
  rejected candidates, RNG seeds, internal job IDs. The TCKDB
  upload is the *result the workflow stands behind*, not the
  process that produced it.
- **Free-text duplicates of `note` fields elsewhere on the upload.**
  If the upload has a `Conformer.note`, a `Thermo.note`, or a
  `TransitionState.note`, put the relevant text there — those *do*
  ride on the wire today.

If a note would be the *only* mechanism preserving an important
fact, that's a strong signal a structured field is missing. File
it instead of stuffing the note.

---

## 5. Relationship to artifacts

**Artifacts** are the correct place for input / output files,
logs, checkpoint files, and reproducibility evidence. A note may
*point* at an artifact ("converged in 47 SCF cycles; full log
attached"), but it must not *duplicate* artifact content.

The pattern that works:

| Information                                | Goes in            |
|--------------------------------------------|--------------------|
| The bytes of the Gaussian output log       | `add_artifact(...)`|
| Why the producer thinks the log matters    | `Calculation.note` |
| The Gaussian version that wrote the log    | `SoftwareRelease`  |
| Whether the calc terminated normally       | `Calculation.converged` / structured result blocks |
| A hash of the artifact bytes               | computed server-side at upload time |

The note is the **one-line index entry** for the calculation; the
artifact is the **archive** behind it.

---

## 6. Relationship to the conformer boundary

The boundary set in
[`conformer_semantic_boundary.md`](conformer_semantic_boundary.md)
applies to notes too. The note field is **not** a backdoor for
candidate-conformer narratives.

Rules:

- Do **not** use notes to smuggle conformer candidate lists into
  TCKDB. ("Conformer 3 of 12 from CREST" — *no*. The upload
  contains one converged structure; the search history is an
  artifact, not a note string.)
- Do **not** use notes to encode a *workflow-preferred-from-N*
  narrative. The upload represents one scientifically meaningful
  conformer; if you find yourself writing "selected from 17", the
  upload already says everything the wire needs.
- Do **not** use notes to invent the per-record discriminator the
  conformer-boundary policy explicitly rejects (the field whose
  rejection the boundary doc names by token). No
  `"<rejected-flag>=true"`-shaped strings, no `"this is the
  lowest-energy of N candidates"` narratives that imply other
  records should exist.

If a workflow considered multiple conformers, upload the
scientifically meaningful result the workflow stands behind, keep
search details in artifacts if needed, and — if a one-line
acknowledgement helps a future reader — write something like
*"CREST pruning produced the geometry shipped here"* and stop.

---

## 7. Future backend support

Backend schema support for `Calculation.note` **may be added later**
in a separate piece of work; it is not assumed by anything in this
document. If the field is later emitted on the wire, the following
constraints are likely to apply (this is a non-binding sketch,
written so producer adapters can be defensive today):

- **Short text only.** Likely length-capped (e.g. 512 or 1024 char
  ceiling).
- **Not indexed as scientific identity.** The note is metadata, not
  a primary key. It will not appear in lookup / dedupe paths.
- **Not used for deduplication.** Two calculations identical in
  every structured field but with different notes are still two
  records; or, more likely, are one record with a curator-merged
  note. Either way, the note doesn't make them distinct.
- **Not a substitute for structured provenance.** Even after the
  wire field lands, every rule from §1–§6 still holds: structured
  fields first, notes as the one-line "why" on top.
- **Curation-facing, not search-facing.** Notes will surface in
  per-record views (admin / curator / data-quality tooling) — not
  in default scientific search results.

Producer adapters that respect these conventions today will
need no changes when the field becomes emitted; adapters that
treat the note as a smuggle channel will surface as data-quality
issues at curation time.

---

## 8. Non-goals

Reiterated for emphasis:

- **No backend schema change** in this document.
- **No wire emission.** This conventions doc does not turn the
  field on; the client's `to_payload()` behaviour is unchanged.
- **No parser implementation.** Notes are produced by adapters
  and humans, not by file parsers (see
  [`parser_validation_boundary.md`](parser_validation_boundary.md)).
- **No ARC changes.** ARC's `TCKDBAdapter` is the right place to
  decide *what* to put in a note for ARC-shaped runs; this doc
  governs the field, not any specific adapter.
- **No attempt to standardise all possible note vocabulary.** A
  controlled vocabulary belongs in a future curated-tag field, not
  in free text. If a producer wants tags, they should ask for a
  structured field rather than overload `note`.

---

## See also

- [`builder_api_mvp.md`](builder_api_mvp.md) — the builder spec;
  factory signatures (including the `note=` kwarg shipped in
  `tckdb-client` 0.26.3) live there.
- [`conformer_semantic_boundary.md`](conformer_semantic_boundary.md)
  — the policy that this document's §6 inherits from.
- [`parser_validation_boundary.md`](parser_validation_boundary.md)
  — the broader layering that puts notes (a producer/adapter
  concern) above the builder layer and below curation.
- [`builder_summary_design.md`](builder_summary_design.md) — why
  the summary surface intentionally does not surface notes.
