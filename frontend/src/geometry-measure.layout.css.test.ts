import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-viewer.css.test.ts for why).
import css from "./geometry-measure.css?raw"

/** Extracts the declaration block for a single, non-nested selector. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in geometry-measure.css`)
    return match[1]
}

/**
 * The measurements panel sits directly under the 3D picture: it must not
 * use `margin: 0 auto`, which would centre it in the FULL page column while
 * `.viewer-stage` (the canvas's own card, `geometry-viewer.css`) is
 * left-aligned at a narrower width.
 *
 * jsdom cannot lay anything out, so it cannot see the actual floating --
 * that was confirmed with a real Chromium instance (see the PR body's
 * before/after CDP rects). What CAN be checked without a browser is that
 * the centring declaration itself is gone.
 */
describe(".viewer-measurements (defect: floats right of/below the picture)", () => {
    const rule = extractRule(css, ".viewer-measurements")

    it("no longer carries `margin: 0 auto` (which centred it in the wrong column)", () => {
        expect(rule).not.toMatch(/margin:\s*0\s+auto/)
    })

    it("uses `margin: 0` so it shares the stage card's left edge", () => {
        expect(rule).toMatch(/margin:\s*0\s*;/)
    })

    it("keeps the same max-width as before (36rem) so it still reads as one column", () => {
        expect(rule).toMatch(/max-width:\s*36rem/)
    })
})
