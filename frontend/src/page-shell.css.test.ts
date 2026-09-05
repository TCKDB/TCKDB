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
 * BLOCKING review finding (PR 366): at <=62rem, `.page-shell-layout`
 * switches to `flex-direction: column`, but its base rule's
 * `align-items: flex-start` (correct for the WIDE row layout's CROSS
 * axis) was never reset for the narrow layout's now-different cross axis
 * (horizontal, in a column flex container). `flex-start` on that axis
 * means "size to content, don't stretch" -- so `.page-shell-content`
 * (base rule: `flex: 1 1 0; min-width: 0`, both meaningful only on the
 * MAIN axis) shrank to its own max-content width the moment any child
 * had unconstrained intrinsic width, which a `.data-table` (no forced
 * width, unlike the retired `.stage-table`) supplies routinely. MEASURED
 * on the species-entry page before this fix: `document.documentElement
 * .scrollWidth` at 680px was up to 1176px, with every `.table-scroll`
 * itself reporting `scrollWidth == clientWidth` -- proof the PAGE was
 * widening around the table, not the table scrolling inside a
 * fixed-width page, the opposite of what `.table-scroll` exists for.
 *
 * jsdom does not lay out or paint anything (no flexbox sizing, no
 * intrinsic-content-width measurement), so it CANNOT see the visual bug
 * this fix exists to prevent, and a component/DOM-structure test run
 * under jsdom would pass identically whether this CSS rule is present,
 * absent, or reverted -- the same limitation `species-entry.css.test.ts`'s
 * own header comment documents for the subgrid alignment it pins. This
 * is therefore a CSS-source-level pin (raw declaration text, same
 * technique as every other test in this file) -- the minimum a
 * regression test can do here, not a substitute for the real check,
 * which is the layout measurement above (rendered in a real browser,
 * screenshotted, and reported in the PR).
 */
describe("<=62rem: .page-shell-content stretches to fill the column layout's cross axis (blocking fix, PR 366)", () => {
    const narrowBlock = extractBlock(css, /@media \(max-width: 62rem\) \{/)

    it("declares .page-shell-content inside the narrow media query", () => {
        expect(narrowBlock).toMatch(/\.page-shell-content\s*\{/)
    })

    it("overrides the inherited (wide-layout) align-items: flex-start with align-self: stretch, so this item fills the column's width instead of shrinking to its own content", () => {
        const rule = extractRule(narrowBlock, ".page-shell-content")
        expect(rule).toMatch(/align-self:\s*stretch/)
    })

    it("also sets an explicit width: 100% -- align-self: stretch alone is not sufficient in every engine, and this is the belt-and-braces companion", () => {
        const rule = extractRule(narrowBlock, ".page-shell-content")
        expect(rule).toMatch(/width:\s*100%/)
    })

    it("restates min-width: 0 here too, so a wide child's intrinsic content width still cannot win over the 100% this rule asks for", () => {
        const rule = extractRule(narrowBlock, ".page-shell-content")
        expect(rule).toMatch(/min-width:\s*0/)
    })
})
