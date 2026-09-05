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

    it(".ref-item a / .ref-item-value keep overflow-wrap: anywhere, not word-break: keep-all (post-review pass)", () => {
        // `word-break: keep-all` was added, then retired: it only ever
        // affects CJK line breaking, so it changed nothing for these
        // refs, and this component's ref values are not run through a
        // <wbr>-inserting helper (`RefsDisclosure.tsx` is shared outside
        // this PR's scope) for it to have preferred anyway. `overflow-
        // wrap: anywhere` stays -- it is what a value with no break
        // opportunity wraps at instead of overflowing this box; switching
        // it to `normal` was the caught regression `design-system.css`'s
        // matching `.kv-list dd` test also guards.
        expect(refItemValueRule![1]).toMatch(/overflow-wrap:\s*anywhere/)
        expect(refItemValueRule![1]).not.toMatch(/overflow-wrap:\s*normal/)
    })

    it("word-break: keep-all does not appear anywhere in refs-disclosure.css", () => {
        expect(css).not.toMatch(/word-break:\s*keep-all/)
    })

    it(".copy-button uses --type-ui (was .58rem, under the 11px floor)", () => {
        const rule = extractRule(css, ".copy-button")
        expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
        expect(rule).not.toMatch(/\.58rem/)
    })
})

/**
 * SF-2 (post-review, "header copy and inset disclosure" PR): `.refs-
 * disclosure` used to override `border-color: var(--line-2)`, a
 * slightly different grey from `.disclosure`'s own `--line` -- MEASURED,
 * that meant the site still rendered THREE distinct disclosure boxes
 * (the base `.disclosure`, this `--line-2` variant, and the
 * `.disclosure--inset` modifier) even after the inset modifier was
 * named, one more than the two the owner wants. Dropped: this class now
 * owns only `margin-top`, and its border colour comes entirely from
 * `.disclosure` -- pinned here (deleting the rule and reverting turns
 * BOTH of these red, sha256-verified restore per the worktree brief).
 */
describe("SF-2: .refs-disclosure has no border-color divergence -- the References box uses the same --line as every other disclosure", () => {
    it("declares no border-color (and no border shorthand) of its own", () => {
        const rule = extractRule(css, ".refs-disclosure")
        expect(rule).not.toMatch(/border-color/)
        expect(rule).not.toMatch(/\bborder\s*:/)
    })

    it("its own rule is margin-top only -- nothing left to diverge from .disclosure's box on", () => {
        const rule = extractRule(css, ".refs-disclosure")
        expect(rule.replace(/\s+/g, " ").trim()).toBe("margin-top: 1.75rem;")
    })
})
