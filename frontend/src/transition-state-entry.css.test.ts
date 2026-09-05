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

/**
 * BLOCKING-1 ("record-page residuals" re-review): the IRC point list's
 * grid cells (176px, `minmax(11rem, 1fr)`) were narrower than a 31-char
 * mono ref, printing column 1 over column 2 -- MEASURED `scrollWidth` 714
 * at a 680px viewport. Widened to `minmax(18rem, 1fr)`, plus an explicit
 * single-column breakpoint below 62rem (rather than relying on auto-fill
 * to happen to resolve to one column on its own) and `min-width: 0` on
 * each item so a still-too-wide ref shrinks/wraps inside its own cell
 * instead of widening the grid.
 */
describe("IRC point list: wide-enough cells, forced single column below 62rem, shrinkable items", () => {
    const stripped = stripComments(css)

    it("widened the grid cell to 18rem (was 11rem)", () => {
        expect(stripped).toMatch(/\.tse-irc-point-list\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(18rem,\s*1fr\)\)/)
        expect(stripped).not.toMatch(/minmax\(11rem,\s*1fr\)/)
    })

    it("forces a single column below 62rem", () => {
        expect(stripped).toMatch(/@media \(max-width: 62rem\) \{\s*\.tse-irc-point-list\s*\{\s*grid-template-columns:\s*1fr;/)
    })

    it("lets each list item shrink below its content's min-content width", () => {
        expect(stripped).toMatch(/\.tse-irc-point-list li\s*\{[^}]*min-width:\s*0/)
    })
})

/**
 * SHOULD-FIX-2 ("record-page residuals" re-review): the saddle-point
 * verdict box had a top margin only (`var(--s-4) 0 0`); the `.kv-list`
 * immediately following it owns no top margin of its own (the shared
 * primitive's contract), so the two sat 10px apart -- MEASURED. It also
 * carried a one-off `.92rem/1.55 serif` face outside the 13-step scale.
 */
describe("saddle-point box: owns spacing on both sides, on-scale typography", () => {
    const stripped = stripComments(css)
    const rule = /\.tse-saddle-point\s*\{([^}]*)\}/.exec(stripped)

    it("rule exists", () => {
        expect(rule, ".tse-saddle-point rule not found").not.toBeNull()
    })

    it("margin sets both a top AND a bottom gap", () => {
        expect(rule![1]).toMatch(/margin:\s*var\(--s-4\)\s+0\s+var\(--s-5\)/)
    })

    it("uses the shared --type-body-font step, not a one-off size/face", () => {
        expect(rule![1]).toMatch(/font:\s*var\(--type-body-font\)/)
        expect(rule![1]).not.toMatch(/\.92rem/)
        expect(rule![1]).not.toMatch(/var\(--serif\)/)
    })
})

/**
 * SHOULD-FIX-4 ("record-page residuals" re-review): 126 characters per
 * line, no cap -- the IRC summary paragraph never composed the shared
 * `--measure-note` token every other genuine-prose block on this page's
 * ledger vocabulary uses.
 */
describe("IRC summary paragraph is capped to --measure-note", () => {
    it("declares max-width: var(--measure-note)", () => {
        const rule = /\.tse-irc-summary p\s*\{([^}]*)\}/.exec(stripComments(css))
        expect(rule, ".tse-irc-summary p rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/max-width:\s*var\(--measure-note\)/)
    })
})
