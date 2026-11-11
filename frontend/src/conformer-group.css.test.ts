import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-detail.css.test.ts for why).
import css from "./conformer-group.css?raw"

/**
 * Extracts the declaration block for a single, BARE, non-nested selector
 * (the selector alone at the start of its own line) -- anchored to line
 * start so a query for `.ledger-summary--single` cannot accidentally
 * match inside the unrelated compound selector
 * `.ledger-summary + .ledger-summary--single` (record-summary-row PR
 * added that sibling-combinator rule; a plain substring search would
 * find IT first, since it appears earlier in the file).
 */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`^[ \\t]*${escaped}\\s*\\{([^}]*)\\}`, "m").exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in conformer-group.css`)
    return match[1]
}

describe("conformer-group.css: geometry groups let grid items shrink", () => {
    // Grid items default to min-width: auto; an opened disclosure with a wide
    // table widened the page at 680 instead of scrolling (PR B review).
    it("sets min-width: 0 on every direct child of .geometry-groups", () => {
        expect(css).toMatch(/\.geometry-groups > \* \{ min-width: 0; \}/)
    })
})

/**
 * SHOULD-FIX-7 ("record-page residuals" re-review): `.section-intro` was
 * `margin: 0` -- the next `dt` sat 20px above where it visually should,
 * since the `.kv-list` that usually follows owns no top margin of its
 * own (the shared primitive's contract).
 */
describe(".section-intro owns a bottom gap to whatever follows it (SHOULD-FIX-7)", () => {
    it("declares margin: 0 0 var(--s-4)", () => {
        const rule = extractRule(css, ".section-intro")
        expect(rule).toMatch(/margin:\s*0 0 var\(--s-4\)/)
    })
})

describe(".ledger-summary is a single full-width card", () => {
    it("declares one column and no tile-row grid, media query or sibling rule", () => {
        const rule = extractRule(css, ".ledger-summary")
        expect(rule).toMatch(/grid-template-columns:\s*1fr/)
        expect(css).not.toMatch(/repeat\(3,\s*1fr\)/)
        expect(css).not.toMatch(/@media \(max-width: 72rem\)/)
        expect(css).not.toMatch(/\.ledger-summary\s*\+/)
    })

    it(".ledger-summary--single owns the gap to the section after it", () => {
        expect(extractRule(css, ".ledger-summary--single")).toMatch(/margin-bottom:\s*3\.5rem/)
    })

    it("no longer declares .coverage-card or .coverage-checklist -- moved to evidence-checklist.css", () => {
        expect(css).not.toMatch(/\.coverage-card\s*\{/)
        expect(css).not.toMatch(/\.coverage-checklist\s*\{/)
    })
})

describe("copy buttons on the record pages are tap-sized", () => {
    it("declares min-height 2.75rem", () => {
        expect(extractRule(css, ".conformer-page .copy-button")).toMatch(/min-height:\s*2\.75rem/)
    })
})

/**
 * SHOULD-FIX-4/10 (re-review): `.empty-projection` was `.9rem` with no
 * width cap -- the `CalculationDetailPage` "Not recorded on this
 * calculation: …" instance of this shared class ran 140 characters per
 * line, MEASURED.
 */
describe(".empty-projection: capped width, on-scale typography (SHOULD-FIX-4/10)", () => {
    it("declares max-width: var(--measure-note) and font: var(--type-note-font)", () => {
        const rule = extractRule(css, ".empty-projection")
        expect(rule).toMatch(/max-width:\s*var\(--measure-note\)/)
        expect(rule).toMatch(/font:\s*var\(--type-note-font\)/)
        expect(rule).not.toMatch(/\.9rem/)
    })
})

/**
 * SHOULD-FIX-13 (re-review): `.observation-list > li` (the sibling-
 * observation rows on `ConformerObservationPage`) used to be a bordered
 * `.78rem mono` box PER sibling -- ten single-line cards in a row.
 */
describe(".observation-list > li is a plain divided row, not a bordered card (SHOULD-FIX-13)", () => {
    it("no longer declares its own border/background/font shorthand", () => {
        const rule = extractRule(css, ".observation-list > li")
        expect(rule).not.toMatch(/\bborder:\s*1px solid var\(--line\);/)
        expect(rule).not.toMatch(/background:/)
        expect(rule).not.toMatch(/font:\s*\.78rem/)
        expect(rule).toMatch(/border-bottom:\s*1px solid var\(--line-2\)/)
    })

    it("the ref link uses --type-data-font, the shared .data step", () => {
        const rule = extractRule(css, ".observation-list > li a")
        expect(rule).toMatch(/font:\s*var\(--type-data-font\)/)
    })
})
