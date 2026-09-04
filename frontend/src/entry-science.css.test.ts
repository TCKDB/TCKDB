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

describe("`.evidence-full-checklist`/dead `.section-heading` are retired onto shared primitives", () => {
    it("declares no `.evidence-full-checklist` rule -- the disclosure box/chevron/summary chrome is the shared `Disclosure` component's `.disclosure` now", () => {
        expect(stripComments(css)).not.toMatch(/\.evidence-full-checklist/)
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
