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

/**
 * SHOULD-FIX-11 ("record-page residuals" re-review): `margin: 0 auto`
 * centred the viewer picture and its caption inside the page while every
 * other block on this page sits flush left -- MEASURED ~350px gutter at
 * 1920. Both are left-aligned now (`margin: 0`).
 */
describe(".viewer-stage / .viewer-caption are left-aligned, not centred (SHOULD-FIX-11)", () => {
    it(".viewer-stage no longer auto-centres", () => {
        const rule = extractRule(css, ".viewer-stage")
        expect(rule).toMatch(/margin:\s*0;/)
        expect(rule).not.toMatch(/margin:\s*0 auto/)
    })

    it(".viewer-caption no longer auto-centres", () => {
        const rule = extractRule(css, ".viewer-caption")
        expect(rule).toMatch(/margin:\s*0;/)
        expect(rule).not.toMatch(/margin:\s*0 auto/)
    })
})

/**
 * SHOULD-FIX-10 ("record-page residuals" re-review): the viewer's own
 * button vocabulary (`.72rem uppercase mono`) was used nowhere else on
 * the site -- mapped onto `--type-ui-font`, the sans step every other
 * button/toggle in this app (the chart's own controls included) uses.
 */
describe("viewer buttons/legends use --type-ui, not a one-off uppercase mono (SHOULD-FIX-10)", () => {
    it(".viewer-controls button / .viewer-style-choice button use --type-ui-font", () => {
        const rule = /\.viewer-controls button,\s*\.viewer-style-choice button \{([^}]*)\}/.exec(css)
        expect(rule, "rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/font:\s*var\(--type-ui-font\)/)
        expect(rule![1]).not.toMatch(/text-transform:\s*uppercase/)
    })

    for (const selector of [".viewer-style-choice legend", ".viewer-label-choice", ".coordinate-toggle button"]) {
        it(`${selector} uses --type-ui-font, no uppercase transform, no .72rem literal`, () => {
            const rule = extractRule(css, selector)
            expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
            expect(rule).not.toMatch(/text-transform:\s*uppercase/)
            expect(rule).not.toMatch(/\.72rem/)
        })
    }

    it(".viewer-label-choice select uses --type-ui-font", () => {
        const rule = extractRule(css, ".viewer-label-choice select")
        expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
        expect(rule).not.toMatch(/\.72rem/)
    })
})
