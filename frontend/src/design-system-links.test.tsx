import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
// Side-effect import so this test's own render tree gets the real,
// compiled stylesheet applied by jsdom -- this file's className usage
// below (`.kv-list`, `.data-table`, `.card`, `.note`) does not otherwise
// pull these rules in on its own the way a page under test does via
// `main.tsx`.
//
// `design-system.css` ONLY, never `index.css` alongside it: MEASURED
// here (see the worktree's own investigation) -- vitest's `css: true`
// test pipeline injects each imported stylesheet as its OWN, SEPARATE
// `<style>`/`CSSStyleSheet`, and does not resolve `@import` within
// either one (a real bundled build does; this test environment does
// not). Importing BOTH `design-system.css` and `index.css` together
// therefore does not reproduce "one cascade, `design-system.css` first"
// the way the real app loads them -- it produces TWO independent sheets,
// and jsdom's cross-sheet cascade resolution does not correctly apply
// CSS specificity across that boundary (a bare `a { color: inherit }`
// from the SECOND sheet was observed beating the higher-specificity
// `.kv-list a { color: var(--accent) }` from the first, which never
// happens in a real browser). Importing `design-system.css` alone
// sidesteps the cross-sheet issue entirely: every assertion below
// either reflects this file's OWN rule, or (for the "outside a
// primitive" case) jsdom's own UA default for a bare `<a>` -- and only
// this file's own rule can produce `color: var(--accent)` /
// `text-decoration-color: var(--accent-underline)`, so those two
// values alone are enough to prove the rule fired.
import "./design-system.css"

afterEach(cleanup)

/**
 * PR B review (BLOCKING-2, "record pages" consolidation): `.record-
 * context a` / `.basin-context a` used to give a link inside a record
 * page's header `dl` the house link treatment (accent colour, an
 * underline in `--accent-underline`); both selectors are retired now
 * that those `dl`s compose the shared `.kv-list` primitive instead, and
 * neither `.kv-list` nor `.data-table`/`.card`/`.note` carried an `a`
 * rule of their own -- `index.css`'s global default (`a { color: inherit;
 * text-decoration: none }`) made a `calc_…`/`co_…`/RECORD-column link
 * inside any of them render indistinguishable from plain text. This
 * checks the ACTUAL rendered computed style (not just the raw CSS text),
 * the same way a reader would actually see it -- a regex match on the
 * stylesheet source could pass while the rule never actually applied
 * (wrong specificity, wrong load order, a typo'd selector).
 *
 * jsdom's CSSOM does not resolve CSS custom properties (no `var()`
 * substitution -- a documented jsdom limitation, see the identical note
 * on `GeometryDetailPage.test.tsx`'s own prose-measure check), so
 * `color`/`text-decoration-color` report the raw, unresolved token
 * reference rather than a computed hex value. That is still a real,
 * mutation-sensitive assertion: it pins WHICH token the rule points at.
 * `text-decoration` (the shorthand property) is asserted instead of the
 * `text-decoration-line` longhand -- ALSO MEASURED: jsdom's `cssstyle`
 * does not expand a `text-decoration: underline` shorthand declaration
 * into the `text-decoration-line` longhand getter (it stays empty),
 * even though it preserves the shorthand's own raw text faithfully.
 */
describe("a link inside a shared primitive gets the house link treatment", () => {
    const cases: [string, string][] = [
        ["kv-list", "kv-list"],
        ["data-table", "data-table"],
        ["card", "card"],
        ["note", "note"],
    ]

    for (const [label, className] of cases) {
        it(`.${label} a computes accent colour and an underline`, () => {
            const { container } = render(
                <div className={className}>
                    <a href="/calculations/calc_demo">calc_demo</a>
                </div>,
            )
            const link = container.querySelector("a") as HTMLAnchorElement
            expect(link).not.toBeNull()
            const style = getComputedStyle(link)
            expect(style.color).toBe("var(--accent)")
            expect(style.textDecoration).toBe("underline")
            expect(style.textDecorationColor).toBe("var(--accent-underline)")
        })
    }

    it("mutation check: a link OUTSIDE any of these primitives does not get the treatment", () => {
        const { container } = render(
            <div className="not-a-primitive">
                <a href="/calculations/calc_demo">calc_demo</a>
            </div>,
        )
        const link = container.querySelector("a") as HTMLAnchorElement
        const style = getComputedStyle(link)
        // Neither this file's own rule (a specific, opt-in `.kv-list a`/
        // `.data-table a`/`.card a`/`.note a` selector) nor
        // `index.css`'s global reset is loaded here -- only jsdom's own
        // UA default for a bare `<a>` applies, which is provably NOT the
        // accent token this primitive rule assigns.
        expect(style.color).not.toBe("var(--accent)")
        expect(style.textDecorationColor).not.toBe("var(--accent-underline)")
    })
})
