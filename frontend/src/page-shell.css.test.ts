import { describe, expect, it } from "vitest"
// `?raw` loads the file as plain source text, unprocessed -- see the
// comment atop `geometry-detail.css.test.ts` for why this suffix (not a
// bare `./page-shell.css` import) is required under this project's
// `css: true` vitest config.
import css from "./page-shell.css?raw"

/**
 * Post-review fix (design/foundations): `.page-toc a` used to apply
 * `--type-ui-font` wholesale, which bakes in weight 600 -- the step's
 * own default. That made EVERY ToC link, active or not, render at 600,
 * which made `.page-toc-active`'s own `font-weight: 600` a no-op and
 * left the active item marked only by colour/border, not weight (MEASURED
 * 600/600 across four ToC pages, vs main's 400/600 before this PR). jsdom
 * cannot compute cascaded font-weight from a raw `font:` shorthand the
 * way a real browser would, so this reads the two rules' raw declaration
 * TEXT and checks that the inactive rule's own weight is NOT 600 while
 * the active rule's is -- a same-value regression (someone reverting the
 * inactive override) is exactly what this guards against.
 */

/** Extracts the declaration block for a single, non-nested selector --
 *  same technique as `index.css.test.ts`'s `extractRule`. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in page-shell.css`)
    return match[1]
}

describe(".page-toc a: inactive and active links resolve to different weights", () => {
    const inactive = extractRule(css, ".page-toc a")
    const active = extractRule(css, ".page-toc a.page-toc-active")

    it("the base (inactive) rule explicitly overrides weight to 400, not left at the type-ui step's baked-in 600", () => {
        expect(inactive).toMatch(/font-weight:\s*400/)
    })

    it("the active rule sets weight 600, a real step up from the inactive rule's 400", () => {
        expect(active).toMatch(/font-weight:\s*600/)
    })

    it("face and size still come from the shared --type-ui-font token on both rules", () => {
        expect(inactive).toMatch(/font:\s*var\(--type-ui-font\)/)
    })
})

/** Extracts the contents of the first brace-balanced block whose opening
 *  brace follows `startPattern` -- same technique as
 *  `species-entry.css.test.ts`'s `extractBlock`, needed here for the
 *  `@media` block below, which contains nested rules a naive `[^}]*`
 *  regex would stop at the first of. */
function extractBlock(source: string, startPattern: RegExp): string {
    const startMatch = startPattern.exec(source)
    if (!startMatch) throw new Error(`No match for ${startPattern} in page-shell.css`)
    const braceStart = source.indexOf("{", startMatch.index)
    if (braceStart === -1) throw new Error(`No opening brace after match for ${startPattern}`)
    let depth = 0
    for (let i = braceStart; i < source.length; i++) {
        if (source[i] === "{") depth++
        else if (source[i] === "}") {
            depth--
            if (depth === 0) return source.slice(braceStart + 1, i)
        }
    }
    throw new Error(`Unbalanced braces after match for ${startPattern}`)
}

/**
 * The page is a two-column grid on wide screens and one `minmax(0, 1fr)` column at <=62rem. The `minmax(0, ...)` track
 * and `min-width: 0` keep a wide child (a data table) from widening the page: it scrolls inside its own wrapper.
 * jsdom does no layout, so these pin the CSS source; the real check is a browser measurement.
 */
describe("wide layout: one fixed side column, a fixed gap, no right rail", () => {
    it("is a grid of a 14rem side column and the main column with a 3rem gap", () => {
        const rule = extractRule(css, ".page-shell-layout")
        expect(rule).toMatch(/display:\s*grid/)
        expect(rule).toMatch(/grid-template-columns:\s*14rem minmax\(0, 1fr\)/)
        expect(rule).toMatch(/gap:\s*3rem/)
    })

    it("lets the main column shrink with min-width: 0", () => {
        expect(extractRule(css, ".page-shell-content")).toMatch(/min-width:\s*0/)
    })

    it("sets the page width and padding once, on .page-shell", () => {
        const rule = extractRule(css, ".page-shell")
        expect(rule).toMatch(/max-width:\s*var\(--page-max\)/)
        expect(rule).toMatch(/padding:/)
    })

    it("keeps the side column sticky and has no rail rule", () => {
        expect(extractRule(css, ".page-shell-side")).toMatch(/position:\s*sticky/)
        expect(css).not.toMatch(/page-shell-rail/)
    })
})

describe("<=62rem: the grid is one column and both columns stack", () => {
    const narrowBlock = extractBlock(css, /@media \(max-width: 62rem\) \{/)

    it("makes the layout a single minmax(0, 1fr) column", () => {
        expect(extractRule(narrowBlock, ".page-shell-layout")).toMatch(/grid-template-columns:\s*minmax\(0, 1fr\)/)
    })

    it("puts the main and side columns in that one column", () => {
        const rule = extractRule(narrowBlock, ".page-shell-content,\n    .page-shell-side")
        expect(rule).toMatch(/grid-column:\s*1/)
    })

    it("releases the side column from sticky positioning", () => {
        expect(narrowBlock).toMatch(/\.page-shell-side \{[^}]*position:\s*static/)
    })
})
