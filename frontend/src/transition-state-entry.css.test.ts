import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text, unprocessed -- see the
// comment atop `geometry-detail.css.test.ts` for why this suffix (not a
// bare import) is required under this project's `css: true` vitest config.
import css from "./transition-state-entry.css?raw"

/** Strips `/* ... *\/` block comments so a comment mentioning a class name
 *  in prose (this file's own comments mention `.tse-page` and
 *  `.data-table` repeatedly, describing WHY the rule below is scoped the
 *  way it is) can never be mistaken for the live selector itself --
 *  PR B review finding: without this, `match![1]` below could capture text
 *  reaching back into the preceding doc comment, which also contains the
 *  literal string `.tse-page` -- the test would keep passing even if the
 *  real selector's `.tse-page` prefix were deleted. Same helper
 *  `design-system.css.test.ts`/`value-pill-scope.css.test.ts` each carry
 *  their own copy of, for the same reason. */
function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

/**
 * jsdom does not lay out or paint anything, so it cannot see the visual
 * defect this file guards: a narrow-viewport table squeezing its first
 * ("Stage") column so tightly that short tokens wrap mid-word ("STAG" /
 * "E", "fre" / "q") -- MEASURED at 1100px on the TS entry page once the
 * Energy column (item 9) gave the table a fifth column to share width
 * with. What CAN be checked without a browser is the raw CSS text: that a
 * `white-space: nowrap` rule exists for the first column, and that it is
 * scoped to `.tse-page` rather than a shared (un-owned) table selector --
 * `ConformerObservationPage` and `ConformerGroupPage` also render
 * `.data-table` now, with a different (shorter, denser) first column that
 * may legitimately still need to wrap, so a global edit on the bare
 * `.data-table` selector would risk silently changing their layout too.
 *
 * design/foundations PR B ("record pages" consolidation): this page
 * migrated its own tables from `.stage-table` to the shared `.data-table`
 * primitive (`design-system.css`) -- the selector below moved with it.
 */
describe("data-table first column does not wrap mid-word, scoped to this page only", () => {
    const stripped = stripComments(css)

    it("declares white-space: nowrap for the first column", () => {
        expect(stripped).toMatch(
            /\.data-table\s+t[hd]:first-child[\s\S]{0,40}\{[^}]*white-space:\s*nowrap/,
        )
    })

    it("is scoped under .tse-page, not the bare .data-table selector", () => {
        const match = /([^{]*\.data-table\s+t[hd]:first-child[^{]*)\{/.exec(stripped)
        expect(match, "no first-child data-table rule found").not.toBeNull()
        expect(match![1]).toMatch(/\.tse-page/)
    })

    // Mutation check for the fix above: with comments still in place, this
    // assertion is a false negative waiting to happen -- prepend a comment
    // mentioning ".tse-page" ahead of an UNSCOPED rule and, without
    // `stripComments`, the test above still passes. Stripping first turns
    // that into a real failure.
    it("mutation check: an unscoped rule immediately after a .tse-page-mentioning comment still fails the scope check", () => {
        const trap = "/* not scoped under .tse-page at all */\n.data-table th:first-child { white-space: nowrap; }"
        const match = /([^{]*\.data-table\s+t[hd]:first-child[^{]*)\{/.exec(stripComments(trap))
        expect(match).not.toBeNull()
        expect(match![1]).not.toMatch(/\.tse-page/)
    })

    it("no longer renders the retired .stage-table selector for this page's own first column", () => {
        expect(stripped).not.toMatch(/\.stage-table/)
    })
})
