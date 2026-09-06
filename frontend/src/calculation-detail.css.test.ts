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
 * record-summary-row PR: `.coverage-checklist` and `.ledger-summary--
 * single` moved OUT of this file entirely, into `conformer-group.css`
 * (now the one CSS home for the evidence checklist card every record
 * page renders through `components/EvidenceChecklist.tsx`) -- see
 * `evidenceChecklist.css.test.ts` for the source test covering their
 * actual declarations (single-column grid, `dt`/`dd` deferred to
 * `.kv-list`, no bespoke label/value layout -- the history item 3 used
 * to document here) and their new location. This file, the former sole
 * owner, must not declare either again.
 */
describe(".coverage-checklist / .ledger-summary--single are no longer declared here (moved to conformer-group.css)", () => {
    it("declares no .coverage-checklist rule", () => {
        expect(css).not.toMatch(/\.coverage-checklist\s*\{/)
    })

    it("declares no .ledger-summary--single rule", () => {
        expect(css).not.toMatch(/\.ledger-summary--single\s*\{/)
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
