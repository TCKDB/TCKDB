import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-detail.css.test.ts for why).
import css from "./calculation-detail.css?raw"

/** Extracts the declaration block for a single, non-nested selector. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in calculation-detail.css`)
    return match[1]
}

/**
 * Item 3 ("record-page residuals" re-review): `.coverage-checklist` used
 * to declare its OWN `dt`/`dd`/`> div` rules (a same-line "label: value"
 * flex row, superseding SHOULD-FIX-5's `flex-start` fix below it) -- a
 * small uppercase label and a larger value on one line with different
 * baselines, MEASURED as visually mismatched with every other fact on
 * this page (the identity block's own `.kv-list`, label above value).
 * Fixed by dropping those bespoke rules entirely and letting the `<dl>`
 * (now `className="kv-list coverage-checklist"` in
 * `CalculationDetailPage.tsx`) fall through to `.kv-list`'s own
 * `dt`/`dd` rules (`design-system.css`) -- this file keeps only the
 * card-spacing rule. This guards against the bespoke rules creeping
 * back in, which would win the cascade over `.kv-list`'s (equal
 * specificity, this file loads after `design-system.css`).
 */
describe(".coverage-checklist defers its label/value layout to .kv-list (item 3)", () => {
    it("declares no dt/dd/> div rules of its own any more", () => {
        expect(css).not.toMatch(/\.coverage-checklist\s*>\s*div\s*\{/)
        expect(css).not.toMatch(/\.coverage-checklist\s+dt\s*\{/)
        expect(css).not.toMatch(/\.coverage-checklist\s+dd\s*\{/)
    })

    it("no longer appends a trailing colon to the label", () => {
        expect(css).not.toMatch(/\.coverage-checklist\s+dt::after/)
    })

    it("keeps its own top margin", () => {
        const rule = extractRule(css, ".coverage-checklist")
        expect(rule).toMatch(/margin:\s*var\(--s-3\)\s+0\s+0/)
    })
})

/**
 * Post-review fix: `kv-list`'s own `auto-fit, minmax(16rem, 1fr)` grid
 * puts these two rows side by side at any width comfortably over
 * ~32rem -- MEASURED at 1920, x=207 and x=837, two columns. That is the
 * SAME "wrap around text" shape the original owner report on this card
 * asked to get away from ("this should be a going down list") --
 * `kv-list`'s auto-fit is right for a multi-column fact grid elsewhere
 * on this page, wrong for this always-two-row checklist. `.coverage-
 * checklist` forces one column, overriding `kv-list`'s template (same
 * specificity, this file loads after `design-system.css`, so the local
 * rule wins).
 */
describe(".coverage-checklist stays a single column, always (going-down-list instruction)", () => {
    it("declares grid-template-columns: 1fr, overriding kv-list's auto-fit", () => {
        const rule = extractRule(css, ".coverage-checklist")
        expect(rule).toMatch(/grid-template-columns:\s*1fr/)
    })
})

/**
 * SHOULD-FIX-4 (re-review): 87 characters per line, no cap -- each row is
 * a genuine sentence ("Coarse pass; refined by <ref>.").
 */
describe(".dependency-sentences li is capped to --measure-note (SHOULD-FIX-4)", () => {
    it("declares max-width: var(--measure-note)", () => {
        const rule = extractRule(css, ".dependency-sentences li")
        expect(rule).toMatch(/max-width:\s*var\(--measure-note\)/)
    })
})

/**
 * SHOULD-FIX-10 (re-review): `.section-note` was a one-off `.8rem` --
 * mapped onto the shared `--type-note-font` step, matching
 * `geometry-detail.css`'s byte-identical copy of this same rule.
 */
describe(".section-note uses --type-note-font, not a literal .8rem (SHOULD-FIX-10)", () => {
    it("declares font: var(--type-note-font)", () => {
        const rule = extractRule(css, ".section-note")
        expect(rule).toMatch(/font:\s*var\(--type-note-font\)/)
        expect(rule).not.toMatch(/font-size:\s*\.8rem/)
    })
})

/**
 * SHOULD-FIX-2 (re-review, criterion-6 on the second re-review pass): the
 * label-less-TS-owner ref fallback in `CalculationDetailPage`'s h1 (`<code
 * className="data calc-headline-ref">`) was rendering at `.data`'s own
 * `--type-data-font` size (13px mono) sitting on the baseline of the
 * surrounding 52px serif h1 -- MEASURED. `.calc-headline-ref { font-size:
 * inherit }` is the fix; this is the SOURCE assertion for it. The
 * existing RTL test on `CalculationDetailPage.test.tsx` only pins
 * `toHaveClass("data", "calc-headline-ref")` -- jsdom does not apply
 * stylesheets, so that test cannot see whether the class actually DOES
 * anything, and emptying this very rule still left it green. This is the
 * guard that closes that gap.
 */
describe(".calc-headline-ref tracks the surrounding heading's size (SHOULD-FIX-2)", () => {
    it("declares font-size: inherit", () => {
        const rule = extractRule(css, ".calc-headline-ref")
        expect(rule).toMatch(/font-size:\s*inherit/)
    })
})
