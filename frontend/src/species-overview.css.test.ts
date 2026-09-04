import { describe, expect, it } from "vitest"
// `?raw` loads the file as plain source text, not a processed stylesheet --
// see the comment atop `geometry-detail.css.test.ts` for why this suffix
// (not a bare `./species-overview.css` import) is required under this
// project's `css: true` vitest config, and why `node:fs` is not an option
// for a file under `src/` (no `"node"` entry in `tsconfig.app.json`'s
// `types`).
import css from "./species-overview.css?raw"

/** Extracts the declaration block for a single, non-nested selector, e.g.
 *  `extractRule(css, ".species-header h1")` returns everything between
 *  `.species-header h1 {` and its matching `}`. Throws if the selector
 *  isn't found, so a rename that forgets to update this file fails loudly
 *  rather than silently matching nothing. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector "${selector}" in species-overview.css`)
    return match[1]
}

// PR D (design-system adoption on the index/record pages): this species
// record page used to define the site's only 102px h1 (`font-size: clamp(
// 3.5rem, 8vw, 6.4rem)`), a blue-underlined 48px h2, an off-token divider
// colour, and a bespoke disclosure/pill/kv-list of its own. Every rule below
// pins one of those migrations so a later edit that quietly reverts one
// (e.g. someone re-adds a literal font-size to `.species-header h1`) fails
// here instead of shipping unnoticed.
describe(".species-header h1 -- the site's primary display heading (--type-display-1)", () => {
    it("uses the shared display-1 type-scale step, not its own literal clamp", () => {
        const rule = extractRule(css, ".species-header h1")
        expect(rule).toMatch(/font:\s*var\(--type-display-1-font\)/)
        expect(rule).toMatch(/letter-spacing:\s*var\(--type-display-1-tracking\)/)
        expect(rule).not.toMatch(/font-size:\s*clamp/)
    })

    it("no longer carries the 4px accent left-bar on .species-header", () => {
        const rule = extractRule(css, ".species-header")
        expect(rule).not.toMatch(/border-left/)
    })
})

describe(".entry-index-heading h2 -- the section heading (--type-heading-1), no underline rule", () => {
    it("uses the shared heading-1 step", () => {
        const rule = extractRule(css, ".entry-index-heading h2")
        expect(rule).toMatch(/font:\s*var\(--type-heading-1-font\)/)
    })

    it("the heading row no longer draws the blue border-bottom underline", () => {
        const rule = extractRule(css, ".entry-index-heading")
        expect(rule).not.toMatch(/border-bottom/)
    })
})

describe(".entry-state-group h3 -- heading-2", () => {
    it("uses the shared heading-2 step, not its own clamp", () => {
        const rule = extractRule(css, ".entry-state-group h3")
        expect(rule).toMatch(/font:\s*var\(--type-heading-2-font\)/)
        expect(rule).not.toMatch(/font-size:\s*clamp/)
    })
})

describe(".entry-state-group border -- the shared --line hairline, not the off-token --divider-strong", () => {
    it("uses var(--line)", () => {
        const rule = extractRule(css, ".entry-state-group")
        expect(rule).toMatch(/border-top:\s*1px solid var\(--line\)/)
    })

    it("no longer USES --divider-strong anywhere in this file (a comment may still name it for history)", () => {
        expect(css).not.toMatch(/var\(--divider-strong\)/)
    })
})

describe("the entry-state disclosure is the shared Disclosure primitive, not a bespoke box", () => {
    it("no longer defines its own .entry-state-disclosure box/summary rules", () => {
        expect(css).not.toMatch(/\.entry-state-disclosure/)
    })
})

describe(".entry-card h4 -- heading-2, house link style (no underline except on hover)", () => {
    it("uses the shared heading-2 step, not its own literal size", () => {
        const rule = extractRule(css, ".entry-card h4")
        expect(rule).toMatch(/font:\s*var\(--type-heading-2-font\)/)
        expect(rule).not.toMatch(/font-size:\s*1\.55rem/)
    })

    it("the heading link has no underline at rest", () => {
        const rule = extractRule(css, ".entry-card h4 a")
        expect(rule).toMatch(/text-decoration:\s*none/)
    })

    it("the heading link underlines only on hover", () => {
        const rule = extractRule(css, ".entry-card h4 a:hover")
        expect(rule).toMatch(/text-decoration:\s*underline/)
    })
})

describe("the review-status pill is retired from this file -- .value-pill--muted is now canonical", () => {
    it("no longer defines its own .entry-review rule", () => {
        expect(css).not.toMatch(/\.entry-review\s*\{/)
    })
})

describe("kv-list adoption -- dt/dd typography no longer duplicated per-page", () => {
    it(".species-identity-grid no longer defines its own dt/dd font rules (the shared .kv-list primitive does)", () => {
        expect(css).not.toMatch(/\.species-identity-grid\s+dt/)
        expect(css).not.toMatch(/\.species-identity-grid\s+dd/)
    })

    it(".entry-facts no longer defines its own dt/dd font rules", () => {
        expect(css).not.toMatch(/\.entry-facts\s+dt/)
        expect(css).not.toMatch(/\.entry-facts\s+dd/)
    })

    it(".entry-facts keeps only the flex layout it needs inside .entry-card, not its own grid", () => {
        const rule = extractRule(css, ".entry-facts")
        expect(rule).not.toMatch(/grid-template-columns/)
        expect(rule).toMatch(/flex:\s*1 1 25rem/)
    })
})

describe("identifiers render at the shared data step", () => {
    it(".entry-card code no longer sets its own one-off font run (the .data class on the element supplies it)", () => {
        const rule = extractRule(css, ".entry-card code")
        expect(rule).not.toMatch(/font:/)
    })
})

describe("notes stay under the shared measure", () => {
    it(".entry-index-intro is capped at --measure-note, not a literal rem value", () => {
        const rule = extractRule(css, ".entry-index-intro")
        expect(rule).toMatch(/max-width:\s*var\(--measure-note\)/)
    })
})
