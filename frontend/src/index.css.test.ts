import { describe, expect, it } from "vitest"
// `?raw` loads the file as plain source text, not a processed stylesheet --
// see the comment atop `geometry-detail.css.test.ts` for why this suffix
// (not a bare `./index.css` import) is required under this project's
// `css: true` vitest config, and why `node:fs` is not an option for a file
// under `src/` (no `"node"` entry in `tsconfig.app.json`'s `types`).
import css from "./index.css?raw"

/**
 * `.archive-shell` (the top-level wrapper in `AppShell.tsx`, around
 * header/`<main>`/footer) is the sticky-footer fix: without it the shell
 * was a plain block exactly as tall as its content, so on a short page
 * (e.g. an empty `/species?kind=vdw` result) the footer sat mid-screen
 * with blank viewport below it instead of pinned to the bottom.
 *
 * jsdom does not lay out or paint anything, so it cannot see the actual
 * defect -- there is no `getBoundingClientRect()` worth trusting here.
 * This file only guards that the rule's *declarations* don't silently
 * regress (e.g. someone deletes `flex: 1` off `<main>` while touching
 * something else nearby). The real evidence that the fix works is a
 * real-browser (Playwright/Chromium) measurement, recorded in the PR
 * description that introduced this file, not anything checkable here.
 */

/** Extracts the declaration block for a single, non-nested selector (not
 *  inside an `@media` block) as raw text, e.g. `extractRule(css, ".archive-shell")`
 *  returns everything between `.archive-shell {` and its matching `}`. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in index.css`)
    return match[1]
}

describe(".archive-shell (sticky footer)", () => {
    const rule = extractRule(css, ".archive-shell")

    it("is a column flex container", () => {
        expect(rule).toMatch(/display:\s*flex/)
        expect(rule).toMatch(/flex-direction:\s*column/)
    })

    it("declares min-height, so the shell can reach the viewport bottom on short pages", () => {
        expect(rule).toMatch(/min-height:/)
    })

    it("declares a 100vh fallback before 100dvh, so browsers without dvh support still get a height", () => {
        const vhIndex = rule.search(/min-height:\s*100vh/)
        const dvhIndex = rule.search(/min-height:\s*100dvh/)
        expect(vhIndex).toBeGreaterThanOrEqual(0)
        expect(dvhIndex).toBeGreaterThanOrEqual(0)
        expect(vhIndex).toBeLessThan(dvhIndex)
    })
})

describe(".archive-shell > main (the flexible child that absorbs slack)", () => {
    const rule = extractRule(css, ".archive-shell > main")

    it("declares flex, so it grows to push the footer down on short pages " +
        "without pulling the footer over content on long ones", () => {
        expect(rule).toMatch(/flex:\s*1\b/)
    })
})
