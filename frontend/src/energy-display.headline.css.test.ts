import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-detail.css.test.ts for why).
import css from "./energy-display.css?raw"

/** Extracts the declaration block for a single, non-nested selector. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in energy-display.css`)
    return match[1]
}

/**
 * Owner report ("record-page residuals" re-review, item 2): the
 * calculation page's headline electronic energy ("-348.435045 hartree")
 * rendered at roughly 1.5x body size, mono, next to a small label -- the
 * owner called it "iffy". `.energy-display-value--headline` used to take
 * the `--type-data-large` step; it now takes the SAME `--type-data` step
 * every other value on the page (and `.energy-display-value`, the
 * non-headline sibling class right above it in the same file) already
 * uses. The unit toggle (`.energy-toggle`) is untouched by this fix.
 */
describe(".energy-display-value--headline (defect: oversized headline energy)", () => {
    const rule = extractRule(css, ".energy-display-value--headline")

    it("uses --type-data-font, the ordinary value step, not --type-data-large-font", () => {
        expect(rule).toMatch(/font:\s*var\(--type-data-font\)/)
        expect(rule).not.toMatch(/--type-data-large-font/)
    })

    it("uses --type-data-tracking, not --type-data-large-tracking", () => {
        expect(rule).toMatch(/letter-spacing:\s*var\(--type-data-tracking\)/)
        expect(rule).not.toMatch(/--type-data-large-tracking/)
    })
})

describe(".energy-toggle (unit toggle stays as-is)", () => {
    it("still exists with its button styling untouched by the headline-size fix", () => {
        const rule = extractRule(css, ".energy-toggle button")
        expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
    })
})
