# TCKDB docs

This directory holds the project's curated documentation. The
landing page for each curated subdirectory is linked below.

## Curated subdirectories

| Subdirectory | What lives here |
|---|---|
| [clients/](clients/) | Client-side conventions (`base_url` + `api_key`, target selection, language clients). |
| [contribution-bundles/](contribution-bundles/) | Bundle format and the local-to-hosted submission workflow. |
| [deployment/](deployment/) | Deployment scenarios — local v0, self-hosted single-node, shared private, and HPC client access. Start with [deployment/README.md](deployment/README.md). |
| [guides/](guides/) | How-to guides oriented at users and workflow tools (querying, demo data, cookbook examples). |
| [specs/](specs/) | Tracked specifications for APIs, schemas, and upload semantics. |

## Docs hygiene policy

Curated, tracked docs live **inside** the subdirectories above
(`docs/clients/`, `docs/contribution-bundles/`, `docs/deployment/`,
`docs/guides/`, `docs/specs/`).

Ad-hoc Markdown files written directly at the root of `docs/` (scratch
notes, working drafts, copied transcripts, prompt files) are
**intentionally gitignored**. The only Markdown file tracked at the
root of `docs/` is this README.

The matching rule in `.gitignore`:

```gitignore
docs/*.md
!docs/README.md
docs/audits/
docs/decisions/
docs/roadmaps/
```

If you want a doc to ship in the repo, place it in one of the curated
subdirectories. If a draft lives at `docs/some_idea.md` it will not
be picked up by `git add`, and that's by design.
