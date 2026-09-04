import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text, unprocessed -- see the
// comment atop `geometry-detail.css.test.ts` for why this suffix (not a
// bare import) is required under this project's `css: true` vitest config.
import css from "./transition-state-entry.css?raw"

/**
 * jsdom does not lay out or paint anything, so it cannot see the visual
 * defect this file guards: a narrow-viewport table squeezing its first
 * ("Stage") column so tightly that short tokens wrap mid-word ("STAG" /
 * "E", "fre" / "q") -- MEASURED at 1100px on the TS entry page once the
 * Energy column (item 9) gave the table a fifth column to share width
 * with. What CAN be checked without a browser is the raw CSS text: that a
 * `white-space: nowrap` rule exists for the first column, and that it is
 * scoped to `.tse-page` rather than a shared (un-owned) table selector --
 * `ConformerObservationPage` and `ConformerGroupPage` still render
 * `.stage-table` with a different first column that may legitimately
 * need to wrap, so a global edit there would risk silently changing
 * their layout too.
 *
 * design/foundations PR B ("record pages" consolidation): this page
 * migrated its own tables from `.stage-table` to the shared `.data-table`
 * primitive (`design-system.css`) -- the selector below moved with it.
 */
describe("data-table first column does not wrap mid-word, scoped to this page only", () => {
    it("declares white-space: nowrap for the first column", () => {
        expect(css).toMatch(
            /\.data-table\s+t[hd]:first-child[\s\S]{0,40}\{[^}]*white-space:\s*nowrap/,
        )
    })

    it("is scoped under .tse-page, not the bare .data-table selector", () => {
        const match = /([^{]*\.data-table\s+t[hd]:first-child[^{]*)\{/.exec(css)
        expect(match, "no first-child data-table rule found").not.toBeNull()
        expect(match![1]).toMatch(/\.tse-page/)
    })

    it("no longer renders the retired .stage-table selector for this page's own first column", () => {
        expect(css).not.toMatch(/\.stage-table/)
    })
})
