import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text, unprocessed -- see the
// comment atop `geometry-detail.css.test.ts` for why this suffix (not a
// bare import) is required under this project's `css: true` vitest config.
import css from "./transition-state-entry.css?raw"

/**
 * jsdom does not lay out or paint anything, so it cannot see the visual
 * defect this file guards: a narrow-viewport `.stage-table` squeezing its
 * first ("Stage") column so tightly that short tokens wrap mid-word
 * ("STAG" / "E", "fre" / "q") -- MEASURED at 1100px on the TS entry page
 * once the Energy column (item 9) gave the table a fifth column to share
 * width with. What CAN be checked without a browser is the raw CSS text:
 * that a `white-space: nowrap` rule exists for the first column, and that
 * it is scoped to `.tse-page` rather than the shared (un-owned)
 * `.stage-table` selector -- `ConformerObservationPage` and
 * `ConformerGroupPage` also render `.stage-table` with a different first
 * column that may legitimately need to wrap, so a global edit there would
 * risk silently changing their layout too.
 */
describe("stage-table first column does not wrap mid-word, scoped to this page only", () => {
    it("declares white-space: nowrap for the first column", () => {
        expect(css).toMatch(
            /\.stage-table\s+t[hd]:first-child[\s\S]{0,40}\{[^}]*white-space:\s*nowrap/,
        )
    })

    it("is scoped under .tse-page, not the bare .stage-table selector", () => {
        const match = /([^{]*\.stage-table\s+t[hd]:first-child[^{]*)\{/.exec(css)
        expect(match, "no first-child stage-table rule found").not.toBeNull()
        expect(match![1]).toMatch(/\.tse-page/)
    })
})
