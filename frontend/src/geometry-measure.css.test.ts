import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-detail.css.test.ts for why).
import css from "./geometry-measure.css?raw"

/** Extracts the declaration block for a single, non-nested selector. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in geometry-measure.css`)
    return match[1]
}

/**
 * SHOULD-FIX-10 ("record-page residuals" re-review): `.viewer-measurements-
 * header h3` was `600 .82rem mono` and `.measure-clear-button` was `.68rem`
 * (10.88px, under the 11px accessibility-pass floor) -- both one-off sizes
 * outside the 13-step scale. Mapped onto `--type-ui-font`, matching the
 * button vocabulary `geometry-detail.css`'s own viewer controls now use.
 */
describe("measurements panel heading/button use --type-ui (SHOULD-FIX-10)", () => {
    it(".viewer-measurements-header h3 uses --type-ui-font, not a literal .82rem mono", () => {
        const rule = extractRule(css, ".viewer-measurements-header h3")
        expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
        expect(rule).not.toMatch(/\.82rem/)
    })

    it(".measure-clear-button uses --type-ui-font, not a literal .68rem", () => {
        const rule = extractRule(css, ".measure-clear-button")
        expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
        expect(rule).not.toMatch(/\.68rem/)
    })
})
