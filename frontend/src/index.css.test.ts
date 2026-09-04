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

// PR D (design-system adoption on the index/record pages): the home hero
// and the record-placeholder/not-found pages used to share ONE clamped h1
// size (the site's largest, 5rem/80px), and a second, stale override
// (`.record-placeholder h1 { font-size: 3.6rem }`, further down this same
// file) forced placeholders to an unclamped 57.6px regardless. The two now
// diverge onto their own named type-scale steps, with the stale override
// removed -- every rule below pins one piece of that so a later edit that
// quietly reintroduces a literal size (or the duplicate override) fails
// here instead of shipping unnoticed.
describe(".archive-hero h1 -- display-1, the site's one true display headline", () => {
    it("uses var(--type-display-1-font), not a literal clamp", () => {
        const rule = extractRule(css, ".archive-hero h1")
        expect(rule).toMatch(/font:\s*var\(--type-display-1-font\)/)
        expect(rule).not.toMatch(/font-size:\s*clamp/)
    })
})

describe(".record-placeholder h1 -- display-2, one step down from the hero", () => {
    it("uses var(--type-display-2-font), not a literal clamp", () => {
        const rule = extractRule(css, ".record-placeholder h1")
        expect(rule).toMatch(/font:\s*var\(--type-display-2-font\)/)
    })

    it("is declared exactly once -- no stale duplicate 3.6rem override later in the file", () => {
        const matches = [...css.matchAll(/\.record-placeholder h1\s*\{/g)]
        expect(matches).toHaveLength(1)
        expect(css).not.toMatch(/\.record-placeholder h1\s*\{\s*font-size:\s*3\.6rem/)
    })
})

describe(".tagline -- the one editorial intro, --type-body sized/weighted but serif", () => {
    it("uses var(--type-body-font) capped at --measure-prose", () => {
        const rule = extractRule(css, ".tagline")
        expect(rule).toMatch(/font:\s*var\(--type-body-font\)/)
        expect(rule).toMatch(/max-width:\s*var\(--measure-prose\)/)
    })
})

describe(".accession-rail -- the mono rail, --type-label", () => {
    it("uses var(--type-label-font), not its own one-off .66rem/.1em pair", () => {
        const rule = extractRule(css, ".accession-rail")
        expect(rule).toMatch(/font:\s*var\(--type-label-font\)/)
        expect(rule).toMatch(/letter-spacing:\s*var\(--type-label-tracking\)/)
    })
})

describe(".search-row input/button -- --type-ui", () => {
    it("both use var(--type-ui-font)", () => {
        const inputRule = extractRule(css, ".search-row input")
        const buttonRule = extractRule(css, ".search-row button")
        expect(inputRule).toMatch(/font:\s*var\(--type-ui-font\)/)
        expect(buttonRule).toMatch(/font:\s*var\(--type-ui-font\)/)
    })
})

describe(".destination -- box styling comes from the shared .card primitive", () => {
    it("no longer declares its own border/border-radius/background (only the flex layout .card doesn't supply)", () => {
        const rule = extractRule(css, ".destination")
        expect(rule).not.toMatch(/border:/)
        expect(rule).not.toMatch(/border-radius:/)
        expect(rule).not.toMatch(/background:/)
        expect(rule).toMatch(/display:\s*flex/)
    })
})

describe(".destination h2/p -- heading-2 serif title, --type-body copy", () => {
    it("the title uses var(--type-heading-2-font)", () => {
        const rule = extractRule(css, ".destination h2")
        expect(rule).toMatch(/font:\s*var\(--type-heading-2-font\)/)
    })

    it("the copy uses var(--type-body-font)", () => {
        const rule = extractRule(css, ".destination p")
        expect(rule).toMatch(/font:\s*var\(--type-body-font\)/)
    })
})

describe(".record-placeholder code -- the path/ref chip is a data run, not an accent-tinted chip", () => {
    it("uses var(--type-data-font) and carries no accent background/colour", () => {
        const rule = extractRule(css, ".record-placeholder code")
        expect(rule).toMatch(/font:\s*var\(--type-data-font\)/)
        expect(rule).not.toMatch(/background:\s*var\(--accent-50\)/)
        expect(rule).not.toMatch(/color:\s*var\(--accent-700\)/)
    })
})

describe(".record-placeholder p:last-child -- body copy under the shared prose measure", () => {
    it("uses var(--measure-prose), not a literal 40rem", () => {
        const rule = extractRule(css, ".record-placeholder p:last-child")
        expect(rule).toMatch(/max-width:\s*var\(--measure-prose\)/)
        expect(rule).toMatch(/font:\s*var\(--type-body-font\)/)
    })
})
