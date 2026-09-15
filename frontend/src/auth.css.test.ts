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

/**
 * The account menu's items must LOOK clickable on their own.
 *
 * `index.css` resets every anchor site-wide to `color: inherit;
 * text-decoration: none`, so an `<a>` that inherits gets no colour, no
 * underline and no hit area beyond its own text -- which is exactly how
 * the deleted `/admin` in-page nav looked, and exactly the complaint
 * ("super gross looks") that started this change. Nothing about rendering
 * the menu in jsdom would catch the rule going missing: the items would
 * still be there, still be links, still be announced correctly, and still
 * be invisible as controls. So the rule itself is asserted.
 *
 * Source-level rather than computed-style, for the reason the block above
 * already gives for `.auth-submit`: what is pinned is that the declaration
 * exists and points at the right token. `padding` is included deliberately
 * -- it is what turns each item into a full-width row rather than a word
 * you have to hit exactly.
 */
describe("the account menu carries its own link styling", () => {
    const item = declarationsFor(authCss, ".account-menu-item")

    it("gives each item a colour, since an inherited one is invisible", () => {
        expect(item).toMatch(/color:\s*var\(--ink\)/)
    })

    it("gives each item a row-sized hit area, not just its own text", () => {
        expect(item).toMatch(/display:\s*block/)
        expect(item).toMatch(/width:\s*100%/)
        expect(item).toMatch(/padding:/)
    })

    it("tints the row on hover and on keyboard focus alike", () => {
        // A hover-only affordance leaves a keyboard reader with no way to
        // see which item they are on.
        expect(declarationsFor(authCss, ".account-menu-item:hover")).toMatch(/background:\s*var\(--accent-50\)/)
        expect(declarationsFor(authCss, ".account-menu-item:focus-visible")).toMatch(/outline:/)
    })

    it("caps the popup against the viewport so a phone gets all of it", () => {
        // 400px-wide screens: a fixed min-width with no max would hang the
        // menu off the right edge, since it is right-aligned to a trigger
        // that already sits at the end of the header.
        expect(declarationsFor(authCss, ".account-menu-popup")).toMatch(/max-width:\s*min\(/)
    })
})
