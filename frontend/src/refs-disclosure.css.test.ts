import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-detail.css.test.ts for why).
import rawCss from "./refs-disclosure.css?raw"

/** Strips `/* ... *\/` comments so prose mentioning a retired value/rule
 *  by name (this file's own comments quote `overflow-wrap: anywhere` and
 *  `.6rem`/`.58rem`, describing what changed) can never be mistaken for a
 *  live declaration -- same technique as `transition-state-entry.css.test.ts`'s
 *  `stripComments`. */
function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

const css = stripComments(rawCss)

/** Extracts a single non-nested rule's declaration block. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in refs-disclosure.css`)
    return match[1]
}

/**
 * SHOULD-FIX-14 ("record-page residuals" re-review): three classes here
 * sat below the accessibility-pass 11px floor (`.ref-item-label` .6rem/
 * 9.6px, `.copy-button` .58rem/9.28px) or off the type scale entirely
 * (`.ref-item a`/`.ref-item-value` .72rem) -- fixed at source onto the
 * named steps this file's role already matches, rather than patched from
 * a distance by `index.css`'s retired specificity-trick floor override.
 */
describe("refs-disclosure.css: on-scale typography (SHOULD-FIX-14)", () => {
    it(".ref-item-label uses --type-label (was .6rem, under the 11px floor)", () => {
        const rule = extractRule(css, ".ref-item-label")
        expect(rule).toMatch(/font:\s*var\(--type-label-font\)/)
        expect(rule).not.toMatch(/\.6rem/)
    })

    const refItemValueRule = /\.ref-item a,\s*\.ref-item-value\s*\{([^}]*)\}/.exec(css)

    it(".ref-item a, .ref-item-value rule exists", () => {
        expect(refItemValueRule, ".ref-item a, .ref-item-value rule not found").not.toBeNull()
    })

    it(".ref-item a / .ref-item-value use --type-data (was .72rem, off-scale)", () => {
        expect(refItemValueRule![1]).toMatch(/font:\s*var\(--type-data-font\)/)
        expect(refItemValueRule![1]).not.toMatch(/\.72rem/)
    })

    it(".ref-item a / .ref-item-value prefer a real break boundary, with an overflow-safety fallback", () => {
        // word-break: keep-all makes a hyphen (or, on a page whose ref
        // renderer inserts one, a <wbr>) win first; overflow-wrap:
        // anywhere STAYS as the fallback for a value with no break
        // opportunity at all (this component is shared outside this PR's
        // scope, so its refs are not run through `refWithBreaks`) --
        // switching this to `normal` regressed a bare hash/hex value into
        // overflowing its row instead of wrapping (the same mutation
        // `design-system.css.test.ts`'s matching `.kv-list` fix guards).
        expect(refItemValueRule![1]).toMatch(/word-break:\s*keep-all/)
        expect(refItemValueRule![1]).toMatch(/overflow-wrap:\s*anywhere/)
    })

    it(".copy-button uses --type-ui (was .58rem, under the 11px floor)", () => {
        const rule = extractRule(css, ".copy-button")
        expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
        expect(rule).not.toMatch(/\.58rem/)
    })
})
