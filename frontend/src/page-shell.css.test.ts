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
