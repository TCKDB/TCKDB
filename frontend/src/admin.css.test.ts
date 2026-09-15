import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text -- see the comment atop
// `theme.css.test.ts` for why this suffix is required under this project's
// `css: true` vitest config.
import adminCss from "./admin.css?raw"

/**
 * Record review and Machine findings each name the other in their lede.
 * That cross-reference is the substance of the rename: the two pages were
 * renamed because a reader could not tell them apart, so each has to offer
 * a way to go and look at the other.
 *
 * `index.css` resets every anchor site-wide to `color: inherit;
 * text-decoration: none`, so without a rule of its own that link renders as
 * plain prose -- MEASURED by screenshotting the page before the rule
 * existed: "Machine findings" inside Record review's lede was
 * indistinguishable from the words either side of it. A rendering test
 * would not catch it either; the link would still be present, still be an
 * anchor, still carry the right href, and still look like nothing.
 */
function declarationsFor(css: string, selector: string): string {
    // Comments first: a `/* ... */` block above a rule is part of the same
    // `[^{}]+` run as the selector, so without this every documented rule in
    // this file reads as an unmatchable selector.
    const blocks = [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^}]*)\}/g)]
    return blocks
        .filter((block) => block[1].split(",").some((part) => part.trim() === selector))
        .map((block) => block[2])
        .join(" ")
}

describe("a cross-reference in a page lede looks like a link", () => {
    it("takes the house prose-link treatment, not the site-wide anchor reset", () => {
        const rule = declarationsFor(adminCss, ".admin-lede a")
        expect(rule).toMatch(/color:\s*var\(--accent\)/)
        expect(rule).toMatch(/text-decoration:\s*underline/)
        expect(rule).toMatch(/text-decoration-color:\s*var\(--accent-underline\)/)
    })

    it("is visible to a keyboard reader too", () => {
        expect(declarationsFor(adminCss, ".admin-lede a:focus-visible")).toMatch(/outline:/)
    })
})

describe("the deleted /admin in-page nav left no stylesheet behind", () => {
    it("has no .admin-nav rule", () => {
        // The nav itself is gone (`AdminPage.test.tsx` asserts the page
        // offers no such links). This is the other half: a live rule for a
        // class nothing renders is how a stylesheet accumulates dead weight,
        // and `dead-css-class.test.ts` does not cover this file.
        expect(declarationsFor(adminCss, ".admin-nav")).toBe("")
        expect(declarationsFor(adminCss, ".admin-nav a")).toBe("")
    })
})
