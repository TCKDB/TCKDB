import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text, unprocessed -- see the
// comment atop `geometry-detail.css.test.ts` for why this suffix (not a
// bare import) is required under this project's `css: true` vitest config.
import css from "./transition-state-entry.css?raw"

/** Strips `/* ... *\/` block comments so a comment mentioning a class name
 *  in prose (this file's own comments mention `.tse-page` and
 *  `.data-table` repeatedly, describing WHY the rule below is scoped the
 *  way it is) can never be mistaken for the live selector itself --
 *  Without this, `match![1]` below could capture text
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
 * "E", "fre" / "q") at 1100px on the TS entry page, where the
 * Energy column gives the table a fifth column to share width
 * with. What CAN be checked without a browser is the raw CSS text: that a
 * `white-space: nowrap` rule exists for the first column, and that it is
 * scoped to `.tse-page` rather than a shared (un-owned) table selector --
 * `ConformerObservationPage` and `ConformerGroupPage` also render
 * `.data-table` now, with a different (shorter, denser) first column that
 * may legitimately still need to wrap, so a global edit on the bare
 * `.data-table` selector would risk silently changing their layout too.
 *
 * This page's tables use the shared `.data-table` primitive
 * (`design-system.css`).
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

    it("does not use the .stage-table selector for this page's own first column", () => {
        expect(stripped).not.toMatch(/\.stage-table/)
    })
})

/**
 * The IRC point list's grid cells use `minmax(18rem, 1fr)` (a 31-char mono
 * ref is wider than `minmax(11rem, 1fr)` and would print column 1 over
 * column 2), an explicit single-column breakpoint below 62rem (rather than
 * relying on auto-fill to resolve to one column) and `min-width: 0` on each
 * item so a still-too-wide ref shrinks/wraps inside its own cell instead of
 * widening the grid.
 */
describe("IRC point list: wide-enough cells, forced single column below 62rem, shrinkable items", () => {
    const stripped = stripComments(css)

    it("sets the grid cell to 18rem, not 11rem", () => {
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
 * The saddle-point
 * verdict box owns spacing on both sides: the `.kv-list`
 * immediately following it owns no top margin of its own (the shared
 * primitive's contract), so a box with a top margin only would sit 10px
 * from it. Its typography stays on the type scale.
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
 * 126 characters per
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

/** Trust and IRC sit side by side and stack on narrow screens; tables scroll inside their own box. */
describe("trust and IRC pair, and the scrolling boxes", () => {
    const stripped = stripComments(css)

    it("lays the pair out in two columns", () => {
        expect(stripped).toMatch(/\.tse-pair\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/)
    })

    it("stacks the pair in one column on narrow screens", () => {
        expect(stripped).toMatch(/@media \(max-width: 48rem\) \{\s*\.tse-pair\s*\{\s*grid-template-columns:\s*1fr;/)
    })

    it("lets the pair's cards shrink below their content", () => {
        expect(stripped).toMatch(/\.tse-pair-card\s*\{[^}]*min-width:\s*0/)
    })

    it("gives a scrolling box a focus outline", () => {
        expect(stripped).toMatch(/\.tse-scroll:focus-visible\s*\{[^}]*outline:/)
    })

    it("keeps the first-column nowrap off the checks table, whose first column is a sentence", () => {
        expect(stripped).not.toMatch(/\.tse-checks-table[^{]*:first-child/)
    })

    it("every selector of the nowrap rule sits under .tse-calc-scroll", () => {
        const rules = [...stripped.matchAll(/([^{}]+)\{([^}]*)\}/g)].filter(([, , body]) => /white-space:\s*nowrap/.test(body))
        expect(rules.length, "no nowrap rule found").toBeGreaterThan(0)
        for (const [, selectors] of rules) {
            for (const selector of selectors.split(",")) expect(selector.trim(), selector).toMatch(/\.tse-calc-scroll/)
        }
    })

    it("stacks the checks table below 40rem with the labels shown from data-label", () => {
        expect(stripped).toMatch(/@media \(max-width: 40rem\)[\s\S]*\.tse-checks-table td\[data-label\]::before\s*\{[^}]*content:\s*attr\(data-label\)/)
    })
})
