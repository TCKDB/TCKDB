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
 * to document here) and their location. This file must not declare either.
 */
describe(".coverage-checklist / .ledger-summary--single are not declared here (conformer-group.css declares them)", () => {
    it("declares no .coverage-checklist rule", () => {
        expect(css).not.toMatch(/\.coverage-checklist\s*\{/)
    })

    it("declares no .ledger-summary--single rule", () => {
        expect(css).not.toMatch(/\.ledger-summary--single\s*\{/)
    })
})

/**
 * `.dependency-sentences` is not declared in this file: the sentence list
 * lives in `CalculationDependencyGraph.tsx`, styled by that component's OWN
 * sheet (`calculation-dependency-graph.css`'s `.dep-graph-sentences`, which
 * carries the `max-width: var(--measure-note)` cap -- see
 * `calculation-dependency-graph.css.test.ts`'s "demoted sentence list"
 * tests).
 */
describe(".dependency-sentences is not declared here (calculation-dependency-graph.css declares it)", () => {
    it("declares no .dependency-sentences rule", () => {
        expect(css).not.toMatch(/\.dependency-sentences\s*\{/)
    })
})

/**
 * `.section-note` uses the shared `--type-note-font` step, matching
 * `geometry-detail.css`'s byte-identical copy of this same rule.
 */
describe(".section-note uses --type-note-font, not a literal .8rem", () => {
    it("declares font: var(--type-note-font)", () => {
        const rule = extractRule(css, ".section-note")
        expect(rule).toMatch(/font:\s*var\(--type-note-font\)/)
        expect(rule).not.toMatch(/font-size:\s*\.8rem/)
    })
})

