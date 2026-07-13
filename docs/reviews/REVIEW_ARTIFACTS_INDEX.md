# Review Artifacts Index — July 2026 Publication-Readiness Review

**Purpose of this file:** a table-of-contents so the markdown artifacts produced by the July 2026 multi-agent publication-readiness review stay findable. This is a **navigation doc only** — it does not restate conclusions; follow the links.

**What this review effort was:** a multi-agent pass (2026-07-13) assessing TCKDB v2's readiness for scientific publication — a section-by-section review of the draft manuscript, a codebase capability inventory, a design-novelty referee report, and a competitor-landscape survey. Some outputs are durable files (listed below); some were session-only agent reports whose conclusions were captured directly into project memory rather than a file (also listed below, with a pointer to that memory).

## Paper

| Artifact | Path | Status |
|---|---|---|
| Section-by-section paper review — drop-in draft prose, figure/table plan, claims-safety audit | `paper/18__TCKDB/DRAFT_REVIEW_AND_IMPROVEMENTS.md` | Untracked / local (not yet committed) |

## Plans

| Artifact | Path | Status |
|---|---|---|
| Forward plan to finish the machine-review LLM providers (Off/Cloud/Local) | `docs/plans/machine_review_llm_implementation.md` | Merged, tracked (PR #1) |
| How to present the 98-table schema to the group and in the paper; tooling choices and figure plan | `docs/plans/schema_visualization_and_group_presentation.md` | Added in this housekeeping PR |

## Reference / Background

| Artifact | Path | Status |
|---|---|---|
| Project's shipped-vs-planned ledger for the manuscript (working title, target venues, thesis, section bullets) | `docs/paper/article_outline.md` | Gitignored / local (`docs/paper/` is excluded via `.gitignore`) |

## Memory pointers (session-only agent reports — no file)

The following three review outputs were produced as in-session agent reports during the same 2026-07-13 review and were never written to a file. Their durable conclusions are captured in the project memory **`project_publication_review_2026_07`** (see `~/.claude/projects/-home-calvin-code-TCKDB-v2/memory/project_publication_review_2026_07.md`):

- **Codebase capability inventory** — what's actually built vs. designed (model/schema/service counts, test coverage, which integrations are real vs. stubbed).
- **Design-novelty referee report** — ranked list of headline-worthy claims vs. claims to avoid headlining, checked against prior art.
- **Competitor-landscape survey** — RMG-database, ReSpecTh, RDB7/RGD1, QCArchive, AiiDA/Materials Cloud, and how TCKDB differs (the "living RDB7" framing).

Consult that memory file directly for the conclusions; there is no separate markdown report for these three.
