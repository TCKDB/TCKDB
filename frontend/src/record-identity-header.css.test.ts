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
