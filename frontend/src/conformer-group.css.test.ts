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

/**
 * SHOULD-FIX-9 (re-review): `.metric`'s `8.5rem` min-height left ~70px of
 * empty space in the common case, and the shared 4-column `.ledger-
 * summary` template squeezed labels onto three lines around 1100px.
 */
describe(".metric / .ledger-summary: no reserved empty space, 2x2 before 680 (SHOULD-FIX-9)", () => {
    it(".metric min-height is reduced from 8.5rem", () => {
        const rule = extractRule(css, ".metric")
        expect(rule).toMatch(/min-height:\s*5rem/)
        expect(rule).not.toMatch(/8\.5rem/)
    })

    it(".ledger-summary collapses to a 2x2 grid below 72rem (covers the 1100px MEASURED width), ahead of the 680px single-column breakpoint", () => {
        expect(css).toMatch(/@media \(max-width: 72rem\) \{\s*\.ledger-summary\s*\{\s*grid-template-columns:\s*repeat\(2,\s*1fr\);/)
    })
})

/**
 * record-summary-row PR, item 1: the owner's evidence/coverage card used
 * to render IN THE SAME `.ledger-summary` grid row as the metric tiles
 * ("the box is in line with the other boxes when I thought it would be
 * underneath them") -- it now renders below the tile row, as its own
 * full-width `.ledger-summary--single` section (see
 * `EvidenceChecklist.test.tsx` and each page's own RTL test for the DOM
 * side of this fix). `.ledger-summary` itself goes back to `align-items:
 * stretch` (the SUPERSEDED `start` behaviour, above, existed only to stop
 * tiles being pulled up to a taller CARD sharing the row -- with only
 * same-shaped tiles left in it, `stretch` is what makes every tile in the
 * row the same height) and drops the trailing `1.8fr` card column
 * entirely, down to `repeat(3, 1fr)`.
 */
describe(".ledger-summary is the tile row ONLY now, align-items: stretch (record-summary-row PR, item 1)", () => {
    it("uses align-items: stretch, not start -- every tile in the row is now the same shape", () => {
        const rule = extractRule(css, ".ledger-summary")
        expect(rule).toMatch(/align-items:\s*stretch/)
        expect(rule).not.toMatch(/align-items:\s*start/)
    })

    it("no longer reserves a trailing 1.8fr column for the evidence card", () => {
        const rule = extractRule(css, ".ledger-summary")
        expect(rule).toMatch(/grid-template-columns:\s*repeat\(3,\s*1fr\)/)
        expect(rule).not.toMatch(/1\.8fr/)
    })
})

/**
 * record-summary-row PR, item 1 (post-review fix, review of 2bd17511):
 * MEASURED before the FIRST attempt at this fix, three tiles in one row
 * came out 261×107 / 261×107 / 261×127 -- the third taller only because
 * its own label happened to wrap to two lines. That attempt reserved a
 * fixed two-line height on `.metric span` (the label) via `min-height:
 * 2.6em` -- MEASURED (review of 2bd17511) as ~10px of dead space below
 * the number and a ~10px number displacement on EVERY tile on every page,
 * since no label in the live data actually wraps at 1920. Fixed properly
 * by reordering the markup instead (`<strong>` first, `<span>` after --
 * see each page's own `Metric` component): the number sits at the tile's
 * own top edge by construction, so this file no longer needs -- and must
 * not re-add -- a `min-height` reservation on the label at all.
 */
describe(".metric: number-first markup, no label height reservation (record-summary-row PR, item 1, post-review)", () => {
    it(".metric strong (the number) declares no margin-top -- it is always the tile's first child", () => {
        const rule = extractRule(css, ".metric strong")
        expect(rule).not.toMatch(/margin-top/)
    })

    it(".metric span (the label) declares margin-top: var(--s-2) -- it follows the number, not the reverse", () => {
        const rule = extractRule(css, ".metric span")
        expect(rule).toMatch(/margin-top:\s*var\(--s-2\)/)
    })

    it("declares no min-height reservation on the label, number, or detail line (the superseded fix)", () => {
        // `.metric` ITSELF still legitimately declares `min-height: 5rem`
        // (SHOULD-FIX-9, unrelated to the label-wrap fix this guards) --
        // only the three CHILD elements that would have carried the
        // superseded reservation are checked here.
        for (const selector of [".metric span", ".metric strong", ".metric small"]) {
            const rule = extractRule(css, selector)
            expect(rule, `${selector} must not declare a min-height`).not.toMatch(/min-height/)
        }
    })
})

/**
 * `.ledger-summary--single` stays in THIS file (a `.ledger-summary`
 * variant used directly in page markup, not a class `EvidenceChecklist`
 * itself renders) -- `.coverage-card`/`.coverage-checklist` moved OUT,
 * to the component's own `evidence-checklist.css` (post-review of
 * 2bd17511: the repo convention is a component owns its own stylesheet,
 * the same as `RecordIdentityHeader`/`RefsDisclosure`/`EnergyDisplay`).
 * See `evidenceChecklist.css.test.ts` for the glob-based source test
 * covering both files' declarations.
 */
describe("conformer-group.css owns .ledger-summary and its .ledger-summary--single variant (record-summary-row PR)", () => {
    it("declares .ledger-summary--single as the full-width, one-column variant", () => {
        const rule = extractRule(css, ".ledger-summary--single")
        expect(rule).toMatch(/grid-template-columns:\s*1fr/)
    })

    it("tightens the gap between a tile row and the evidence card that immediately follows it", () => {
        expect(css).toMatch(/\.ledger-summary\s*\+\s*\.ledger-summary--single\s*\{[^}]*margin-top:\s*var\(--s-3\)/)
    })

    it("no longer declares .coverage-card or .coverage-checklist -- moved to evidence-checklist.css", () => {
        expect(css).not.toMatch(/\.coverage-card\s*\{/)
        expect(css).not.toMatch(/\.coverage-checklist\s*\{/)
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
 * SHOULD-FIX-12 (re-review): the "OBSERVATION" card label used
 * `.t-kicker` (no colour rule of its own -> inherited plain `--ink`)
 * while every other label on this page is `--muted`.
 */
describe("observation card label is --muted, matching every other label (SHOULD-FIX-12)", () => {
    it("scopes a --muted colour onto the observation card's first-child label", () => {
        const rule = extractRule(css, ".observation-card header > div:first-child .t-label")
        expect(rule).toMatch(/color:\s*var\(--muted\)/)
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
