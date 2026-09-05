import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-detail.css.test.ts for why).
import css from "./record-identity-header.css?raw"

/**
 * SHOULD-FIX-7 ("record-page residuals" re-review): `.record-identity-
 * note` (`margin: 0`) and the `.kv-list` that follows it inside
 * `.record-identity-known` both own no spacing of their own -- MEASURED
 * 10px baseline-to-cap on calc-freq/geometry pages, tighter than every
 * other tier boundary in this header. `.record-identity-known` now owns
 * the gap between its children directly.
 */
describe(".record-identity-known owns the gap between its children", () => {
    it("is a grid with gap: var(--s-3)", () => {
        const rule = /\.record-identity-known\s*\{([^}]*)\}/.exec(css)
        expect(rule, ".record-identity-known rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/display:\s*grid/)
        expect(rule![1]).toMatch(/gap:\s*var\(--s-3\)/)
    })
})

/**
 * SF-3 (post-review, "header copy and inset disclosure" PR): this rule
 * had no source test of its own. It's what keeps a copy button beside an
 * identity fact's value (SMILES, InChIKey, ...) on the SAME line at
 * 680px rather than pushed onto a line of its own -- `flex` +
 * `align-items: baseline` lays the `<code>` value and the `.copy-button`
 * out side by side, the same pattern `refs-disclosure.css`'s `.ref-item`
 * already uses for a ref row.
 */
describe(".record-identity-fact-copyable lays a value and its copy button out on one line", () => {
    it("is a flex row, baseline-aligned, that can shrink (min-width: 0) rather than force its column wide", () => {
        const rule = /\.record-identity-fact-copyable\s*\{([^}]*)\}/.exec(css)
        expect(rule, ".record-identity-fact-copyable rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/display:\s*flex/)
        expect(rule![1]).toMatch(/align-items:\s*baseline/)
        expect(rule![1]).toMatch(/gap:\s*\.5rem/)
        expect(rule![1]).toMatch(/min-width:\s*0/)
    })
})
