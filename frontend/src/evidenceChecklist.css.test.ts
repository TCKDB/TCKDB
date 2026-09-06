import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-detail.css.test.ts for why).
import calculationDetailCss from "./calculation-detail.css?raw"
import conformerGroupCss from "./conformer-group.css?raw"
import geometryDetailCss from "./geometry-detail.css?raw"

/** Strips `/* ... *\/` comments -- a comment MENTIONING a retired
 *  selector's name in prose (e.g. explaining why it is gone) must not
 *  itself trip a "this is still declared" check. */
function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

/**
 * Extracts the declaration block for a BARE selector rule (the selector
 * alone, at the start of its own line) -- deliberately does NOT match the
 * selector when it appears as part of a compound/combinator selector like
 * `.ledger-summary + .ledger-summary--single`, so a query for
 * `.ledger-summary--single` cannot accidentally pick up that unrelated
 * sibling-combinator rule's block instead of its own standalone one.
 */
function extractRule(source: string, selector: string): string | null {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`^[ \\t]*${escaped}\\s*\\{([^}]*)\\}`, "m").exec(stripComments(source))
    return match ? match[1] : null
}

/**
 * record-summary-row PR: `.coverage-checklist` and `.ledger-summary--
 * single` used to live only in `calculation-detail.css` -- the ONE page
 * that rendered the evidence card before this PR. `.coverage-card`
 * itself has no bespoke rule of its own on any page: its box comes
 * entirely from the generic `.card`/`.card--derived` primitives
 * (`design-system.css`), and its internal layout from `.kv-list` +
 * `.coverage-checklist`'s one-column override -- there is nothing left
 * for a page-specific `.coverage-card { ... }` rule to say (the old
 * `.coverage-card span`/`strong` rules this file used to carry are
 * retired outright: the component never renders a bare `<strong>` any
 * more). Three OTHER page stylesheets now need this same box
 * (`components/EvidenceChecklist.tsx`; `ConformerGroupPage`/
 * `ConformerObservationPage`/`GeometryDetailPage`), so a future page
 * needing it too must have ONE file to reach for -- `conformer-group.css`,
 * the "scientific record ledger" stylesheet already loaded by every one
 * of the four record pages that render the box. This is the source test
 * that keeps a second declaration from creeping back into either of the
 * other two page stylesheets.
 */
const SELECTORS = [".coverage-checklist", ".ledger-summary--single"]

describe("evidence checklist card: one CSS home, in conformer-group.css", () => {
    for (const selector of SELECTORS) {
        it(`conformer-group.css declares ${selector}`, () => {
            expect(extractRule(conformerGroupCss, selector)).not.toBeNull()
        })

        it(`calculation-detail.css no longer declares ${selector} (moved out)`, () => {
            expect(extractRule(calculationDetailCss, selector)).toBeNull()
        })

        it(`geometry-detail.css never declares ${selector}`, () => {
            expect(extractRule(geometryDetailCss, selector)).toBeNull()
        })
    }

    it("no page stylesheet declares a bespoke .coverage-card rule of its own -- the box composes .card/.card--derived", () => {
        for (const css of [calculationDetailCss, conformerGroupCss, geometryDetailCss]) {
            expect(extractRule(css, ".coverage-card")).toBeNull()
        }
    })
})

/**
 * `.validation-card` (`GeometryDetailPage`'s former bespoke evidence-box
 * class, plain `.card` with no `--derived` border) is retired outright,
 * not merged into the shared class -- the page now renders
 * `.card.card--derived.coverage-card` like every other record page (see
 * that page's own JSX comment for why it picks up `--derived`: this box
 * states a COMPUTED verdict about the record, the same axis the other
 * three pages' cards are already on). Nothing should declare it any more.
 */
describe(".validation-card is retired -- no page stylesheet declares it", () => {
    for (const [name, css] of [
        ["calculation-detail.css", calculationDetailCss],
        ["conformer-group.css", conformerGroupCss],
        ["geometry-detail.css", geometryDetailCss],
    ] as const) {
        it(`${name} does not declare .validation-card`, () => {
            expect(stripComments(css)).not.toMatch(/\.validation-card/)
        })
    }
})
