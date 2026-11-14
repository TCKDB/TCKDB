import { describe, expect, it } from "vitest"
// `?raw` = plain source text (see geometry-detail.css.test.ts for why).
import css from "./record-identity-header.css?raw"

/**
 * `.record-identity-
 * note` (`margin: 0`) and the `.kv-list` that follows it inside
 * `.record-identity-known` both own no spacing of their own, which would leave
 * 10px baseline-to-cap on calc-freq/geometry pages, tighter than every
 * other tier boundary in this header. `.record-identity-known` owns
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
 * This rule lays a copy button beside an
 * identity fact's value (SMILES, InChIKey, ...) -- `flex` + `align-items:
 * baseline` puts the `<code>` value and the `.copy-button` side by side,
 * the same pattern `refs-disclosure.css`'s `.ref-item` already uses for a
 * ref row.
 *
 * Without `flex-wrap` the row stays on one line at every width and the
 * VALUE wraps via `.kv-list dd`'s own `overflow-wrap: anywhere` whenever
 * the row doesn't fit -- fine for a short fact, but broken for a 30+ char
 * own ref with a copy button: the `.kv-list` column
 * (`minmax(16rem, 1fr)`, 256px) minus the button (45px) and gap (8px)
 * leaves ~203px for a ~234-242px ref, so the ref ITSELF shrinks to fit and
 * breaks mid-token ("spe_3agdbqfdhkd4yf4seviawkd" / "pla") -- see the
 * next `describe` block.
 */
describe(".record-identity-fact-copyable lays a value and its copy button out side by side", () => {
    it("is a flex row, centred, that can shrink (min-width: 0) rather than force its column wide", () => {
        const rule = /\.record-identity-fact-copyable\s*\{([^}]*)\}/.exec(css)
        expect(rule, ".record-identity-fact-copyable rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/display:\s*flex/)
        expect(rule![1]).toMatch(/align-items:\s*center/)
        expect(rule![1]).toMatch(/gap:\s*\.25rem \.5rem/)
        expect(rule![1]).toMatch(/min-width:\s*0/)
    })
})

/**
 * The value must never break mid-token, and the copy
 * button -- not the ref -- is what gives way at a squeezed column width.
 * `flex-wrap: wrap` on the container lets the button drop to a line of
 * its own below a whole, unbroken ref; `white-space: nowrap` on the
 * value's own `<code>`/`.data` run is what stops it wrapping internally
 * at all (without it, `overflow-wrap: anywhere` from `.kv-list dd` would
 * still be free to split the ref inside a shrunk flex basis -- the exact
 * defect this pins). Remove either declaration and this test
 * goes red.
 */
describe(".record-identity-fact-copyable wraps the BUTTON, never the ref", () => {
    it("is flex-wrap: wrap, so the copy button can drop below the value instead of squeezing it", () => {
        const rule = /\.record-identity-fact-copyable\s*\{([^}]*)\}/.exec(css)
        expect(rule, ".record-identity-fact-copyable rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/flex-wrap:\s*wrap/)
    })

    it("gives its code/.data value white-space: nowrap, so a long ref never breaks mid-token", () => {
        const rule = /\.record-identity-fact-copyable\s+code,\s*\.record-identity-fact-copyable\s+\.data\s*\{([^}]*)\}/.exec(css)
        expect(rule, ".record-identity-fact-copyable code, .record-identity-fact-copyable .data rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/white-space:\s*nowrap/)
    })
})
