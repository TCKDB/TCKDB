import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text, unprocessed -- see the
// comment atop `geometry-detail.css.test.ts` for why this suffix (not a
// bare import) is required under this project's `css: true` vitest config.
import css from "./entry-science.css?raw"

/**
 * design/species-entry (PR C of the design-system consolidation) retired
 * this file's own `.stage-table`/`.review-badge`/`.evidence-chip`/
 * `.evidence-full-checklist`/`.section-note`/`.records-note` reskins in
 * favour of `design-system.css`'s shared primitives (`.data-table`,
 * `.value-pill--muted`, `Disclosure`, `.note`) at each call site in
 * `EntryThermoSection.tsx`/`EntryStatmechSection.tsx`/
 * `EntryTransportSection.tsx` -- these tests pin that retirement so it
 * cannot silently regress back to a page-local reskin of a class the
 * design system already owns.
 */

function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

describe("`.stage-table` is fully retired from this file (data-table migration)", () => {
    it("declares no `.stage-table` rule of its own -- every record table in this file's three tabs renders `.data-table` (design-system.css) now", () => {
        expect(stripComments(css)).not.toMatch(/\.stage-table/)
    })

    it("keeps no narrow-viewport stacked-row reset (`.stage-table`'s old fallback never applied to `.data-table`, so there is nothing left to protect)", () => {
        expect(stripComments(css)).not.toMatch(/min-width:\s*0/)
    })
})

describe("`.review-badge`/`.evidence-chip` are fully retired (one pill style: `.value-pill--muted`)", () => {
    it("declares no `.review-badge` override -- every status pill in this file's three tabs renders `.value-pill--muted` (design-system.css) at its own call site", () => {
        expect(stripComments(css)).not.toMatch(/(?<![\w-])\.review-badge\s*\{/)
    })

    it("declares no `.evidence-chip` rule -- the missing-checklist chip is `.value-pill--muted` now too", () => {
        expect(stripComments(css)).not.toMatch(/(?<![\w-])\.evidence-chip\s*\{/)
    })
})

describe("`.evidence-full-checklist` keeps only spacing; dead `.section-heading` is retired", () => {
    // Review finding (NIT, PR 366): dropping `.evidence-full-checklist`
    // ENTIRELY (this test's own original assertion) left the disclosure's
    // now-visible border sitting flush against `ProvenanceBlock`'s "Level
    // of theory" row immediately below it once `Disclosure` gave it a
    // real box -- harmless as plain text flow before that box existed,
    // visibly broken once it did. The rule stays, margin-only.
    it("keeps a margin-only `.evidence-full-checklist` rule -- no summary/colour/font styling (that's `Disclosure`'s job now), just the spacing `Disclosure` has no opinion on", () => {
        const rule = /\.evidence-full-checklist\s*\{([^}]*)\}/.exec(stripComments(css))
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/margin:/)
        expect(rule![1]).not.toMatch(/color|font|cursor/)
    })

    it("declares no `.evidence-full-checklist summary` rule -- that chrome is `.disclosure > summary`'s job now, not a page-local override", () => {
        expect(stripComments(css)).not.toMatch(/\.evidence-full-checklist\s+summary/)
    })

    it("declares no dead `.section-heading` rule (unused by any component in this file's scope)", () => {
        expect(stripComments(css)).not.toMatch(/(?<![\w-])\.section-heading\b/)
    })
})

describe("block titles (`.model-block-heading`) are a real heading step, never mono", () => {
    it("uses --type-heading-3, not a hardcoded mono font", () => {
        const rule = /\.model-block-heading\s*\{([^}]*)\}/.exec(css)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/font:\s*var\(--type-heading-3-font\)/)
        expect(rule![1]).not.toMatch(/var\(--mono\)/)
    })
})

describe("`.checklist` reads sans/value-weight now, not a blanket mono rule (mono diet)", () => {
    // MEASURED finding this guards: the geometry tab's `<ul className="checklist">`
    // used to mono the ENTIRE row -- the observation summary sentence, "N
    // optimization calculations", level-of-theory labels, atom counts --
    // along with the one or two genuine identifiers it actually needed to.
    // A real identifier now opts back into mono via `.data` at its own
    // span (`ConformerGeometryTab.tsx`); the list's own base rule must
    // never go back to mono for everything under it.
    it("`.checklist`'s own font is --type-value (sans), not var(--mono)", () => {
        const rule = /(?<!li )\.checklist\s*\{([^}]*)\}/.exec(css)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/font:\s*var\(--type-value-font\)/)
        expect(rule![1]).not.toMatch(/var\(--mono\)/)
    })
})

describe("science-record card: one border/radius/padding, owned by `.card` (design-system.css)", () => {
    it("the base `.science-record` rule declares no border, radius, background or padding of its own -- `.card` (added as a second class at every call site) owns the box now", () => {
        const rule = /(?<!\.entry-page )(?<!\+ )\.science-record\s*\{([^}]*)\}/.exec(css)
        expect(rule).not.toBeNull()
        expect(rule![1]).not.toMatch(/border|padding|background|border-radius/)
    })
})

// BLOCKING-2 (species-entry/browse/chrome residuals re-review): the
// "Software"/"Workflow tool" provenance row `IdenticalStatmechGroupRefs`
// (EntryStatmechSection.tsx) renders beneath each record's own row in the
// "Records in this group" table -- moved out of the table's own columns
// so it fits at 1920 without clipping (see that component's own comment).
// Pinned here: reverting this file's own rule to `main` (leaving the
// TSX's `.data-table-provenance-row` class with no styling) is otherwise
// invisible to this test suite -- no other test in this repo reads
// `entry-science.css`'s declarations for this class.
describe(".data-table-provenance-row: the demoted Software/Workflow-tool line under each grouped record", () => {
    it("styles the row at --type-note (muted), with its own bottom border so the two-row group reads as one unit", () => {
        const rule = /\.data-table-provenance-row td\s*\{([^}]*)\}/.exec(css)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/font:\s*var\(--type-note-font\)/)
        expect(rule![1]).toMatch(/color:\s*var\(--muted-2\)/)
        expect(rule![1]).toMatch(/border-bottom:\s*1px solid var\(--line-2\)/)
        expect(rule![1]).toMatch(/padding-top:\s*0/)
    })
})
