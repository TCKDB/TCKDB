import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text -- see the comment atop
// `theme.css.test.ts` for why this suffix is required under this project's
// `css: true` vitest config.
import authCss from "./auth.css?raw"

/**
 * A flex child's `align-self` beats its container's `align-items`, and the
 * two rules live in different places in this file, so nothing about reading
 * either one in isolation reveals the conflict.
 *
 * `.auth-submit` sets `align-self: flex-start` because the SIGN-IN form is a
 * column: the button follows the stacked fields, and flex-start stops it
 * stretching to the card's full width. `.api-key-create` is a ROW with
 * `align-items: flex-end`, meaning "sit the button on the input's baseline".
 * The shared `.auth-submit` rule silently won, so the button rendered level
 * with the "Label" caption, floating above the input it submits.
 *
 * jsdom performs no layout, so this cannot be asserted by rendering. The
 * source-level check is the honest one: the override has to exist.
 */
function declarationsFor(css: string, selector: string): string {
    // Comments must go first: a `/* ... */` block sitting above a rule is
    // part of the same `[^{}]+` run as the selector, so without this every
    // documented rule in this file reads as an unmatchable selector.
    const blocks = [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^}]*)\}/g)]
    return blocks
        .filter((block) => block[1].split(",").some((part) => part.trim() === selector))
        .map((block) => block[2])
        .join(" ")
}

describe("the API key form's submit button", () => {
    it("is not left pinned to the top of the row by the shared .auth-submit rule", () => {
        // The premise: the shared rule really does set flex-start, and the
        // row really does ask for flex-end. If either changes, the override
        // below may no longer be needed and this test should be revisited
        // rather than silently kept passing.
        expect(declarationsFor(authCss, ".auth-submit")).toMatch(/align-self:\s*flex-start/)
        expect(declarationsFor(authCss, ".api-key-create")).toMatch(/align-items:\s*flex-end/)

        // The fix: the row's own button defers to the container again.
        expect(declarationsFor(authCss, ".api-key-create .auth-submit")).toMatch(/align-self:\s*auto/)
    })
})
