import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-detail.css.test.ts for why).
import css from "./conformer-group.css?raw"

/** Extracts the declaration block for a single, non-nested selector. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
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

    // NIT (re-review pass): grid items default to `align-items: stretch`,
    // so at desktop the three `.metric` tiles were pulled up to the
    // evidence/coverage card's own taller content in the same row --
    // MEASURED 188/160/201px -- `.metric`'s `min-height: 5rem` above only
    // ever bound at 680px, where the row is already single-column with no
    // taller sibling to stretch to.
    it(".ledger-summary uses align-items: start so a tile does not stretch to a taller sibling's height", () => {
        const rule = extractRule(css, ".ledger-summary")
        expect(rule).toMatch(/align-items:\s*start/)
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
