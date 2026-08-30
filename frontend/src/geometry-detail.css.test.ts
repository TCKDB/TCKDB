import { describe, expect, it } from "vitest"
// Vite's `?raw` import suffix (typed by the `vite/client` types this app's
// tsconfig already includes) loads the file as a plain string, unprocessed —
// deliberately NOT a bare `./geometry-detail.css` import, which under this
// project's `css: true` vitest config would inject the stylesheet instead of
// handing back its source text. `node:fs` would also work at test-run time,
// but `src/**` is compiled by `tsconfig.app.json`, which has no `"node"`
// entry in `types` (only `tsconfig.node.json`, scoped to `vite.config.ts`,
// does) — `node:fs` fails `tsc -b` for every file under `src/`, this one
// included.
import css from "./geometry-detail.css?raw"

/**
 * jsdom does not lay out or paint anything, so it cannot see either of the
 * two visual defects this file exists to guard: a stray accent bar reads as
 * "a big thick blue ruler line" only in a real, rendered viewport, and the
 * missing right/bottom canvas borders are a browser painting-order artifact
 * (see `.viewer-canvas`'s comment below). Both were verified with Playwright
 * against a real Chromium instance loading the live geometry detail page —
 * not by a computed-layout assertion here, which jsdom could pass having
 * checked nothing. What CAN be checked without a browser is the raw CSS
 * text itself: that the accent-bar declaration does not creep back onto
 * `.viewer-stage`, and that `.viewer-canvas` keeps using `outline` (which
 * `offsetWidth`/`offsetHeight` never include) rather than `border` (which
 * they do, and which is what caused the two hidden borders in the first
 * place — see that rule's comment for the full mechanism).
 */

/** Extracts the declaration block for a single, non-nested selector (not inside
 *  an `@media` block) as raw text, e.g. `extractRule(css, ".viewer-stage")`
 *  returns everything between `.viewer-stage {` and its matching `}`. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in geometry-detail.css`)
    return match[1]
}

describe(".viewer-stage (defect 1: accent bar)", () => {
    const rule = extractRule(css, ".viewer-stage")

    it("does not declare a border-left accent bar", () => {
        expect(rule).not.toMatch(/border-left/)
    })

    it("keeps a plain 1px border on all four sides", () => {
        // `#dbe2ea` was tokenised to `var(--line)` in the theme pass (see
        // `theme.css`) -- same colour, now theme-aware. Matching the token
        // rather than the retired literal keeps this guard aligned with
        // that rename instead of failing on a colour that never regressed.
        expect(rule).toMatch(/border:\s*1px solid var\(--line\)/)
    })
})

describe(".viewer-canvas (defect 2: only 2 of 4 borders visible)", () => {
    const rule = extractRule(css, ".viewer-canvas")

    it("does not declare a `border` (offsetWidth/offsetHeight would include it, " +
        "which is what let 3Dmol's injected canvas overlay the right/bottom edges)", () => {
        expect(rule).not.toMatch(/\bborder:/)
        expect(rule).not.toMatch(/\bborder-\w+:/)
    })

    it("uses `outline` instead, which offsetWidth/offsetHeight never include", () => {
        // See the note on the `.viewer-stage` border test above: `#dbe2ea`
        // is now `var(--line)`.
        expect(rule).toMatch(/outline:\s*1px solid var\(--line\)/)
    })
})
