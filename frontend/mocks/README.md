# Reaction entry page — design-review mock (PR 0)

`reaction-entry.html` and `reaction-overview.html` are static, hand-written pages built for design
review of `docs/plans/reaction-entry-page.md` before any product code exists — every value on them
is either the literal response from `GET /scientific/reaction-entries/rxe_ed66mj3ohtyien5rm2x3sb3rdu/full?include=all`
and `GET /scientific/reactions/search?reaction_ref=rxn_naeqmg4l5wyqex5cl5tir2vt2y` (fetched 2026-09-07),
or explicitly labelled as computed for the mock where the live API does not yet serve a field (formulas;
`§3A` is not deployed). They link the real `src/*.css` stylesheets by relative path and copy the real
components' exact markup shapes (`RecordIdentityHeader`, `EvidenceChecklist`, `ProductLevelsFact`,
`RefsDisclosure`, `CalculationDependencyGraph`, the `ThermoCpChart` frame) so they render through the
production design system with no styling of their own beyond the Arrhenius chart's `<style>` block —
there is no `arrhenius-chart.css` yet, since that ships in PR 3. **PR 2 deletes this directory** once
the real `ReactionEntryPage`/`ReactionOverviewPage` land.

To view them, run `npm run dev` (Vite serves the whole project root, `public/` included, so
`../src/*.css`, `/fonts/...` and `/mocks/reaction-entry.html` all resolve) and open
`http://localhost:5173/mocks/reaction-entry.html` or `/mocks/reaction-overview.html`. A plain static
file server also works for the two HTML files and `../src/*.css`, but only if it additionally maps
`/fonts/`, `/icons.svg` and `/favicon.svg` to `frontend/public/` — those two directories are not one
tree outside of Vite's dev server, which is what makes plain `npx serve frontend` alone incomplete.
