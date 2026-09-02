import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text, unprocessed -- see the
// comment atop `geometry-detail.css.test.ts` for why this suffix (not a
// bare import) is required under this project's `css: true` vitest config.
import css from "./species-entry.css?raw"

/**
 * Conformer card row-track alignment (design/conformer-card-alignment).
 *
 * jsdom does not lay out or paint anything, so it cannot see the visual
 * result this stylesheet change exists to produce -- three cards to a
 * row, meta/coverage lines sitting at the same height across cards, a
 * basin-identity box the same size on every card, references toggles in
 * a straight line. What CAN be checked without a browser, the same way
 * `geometry-detail.css.test.ts` and `theme.css.test.ts` already do for
 * this codebase's other un-renderable visual facts, is the raw CSS text:
 * that the column count is fixed (not floating with viewport width the
 * way `auto-fit` used to leave it), that the subgrid row-sharing
 * declarations exist with a fallback for a browser that lacks them, that
 * every row is assigned an EXPLICIT line number rather than left to
 * auto-placement (the one property that keeps a card missing content in
 * an earlier row from pulling everything below it out of line with its
 * siblings), and that the References-panel border removal is scoped to
 * the conformer card, not the whole `.refs-disclosure` component.
 */

/** Extracts the declaration block for a single, non-nested selector (not
 *  inside an `@media`/`@supports` block) as raw text -- same technique as
 *  `geometry-detail.css.test.ts`'s `extractRule`, extended to tolerate
 *  the file's own whitespace/line-wrapping in a multi-selector list (e.g.
 *  `.conformer-card-meta,\n.conformer-card-coverage {`) rather than
 *  requiring the caller to match indentation exactly: any run of
 *  whitespace in the selector passed in matches any run of whitespace in
 *  the source. */
function extractRule(source: string, selector: string): string {
    const pattern = selector
        .split(/\s+/)
        .filter(Boolean)
        .map((token) => token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
        .join("\\s+")
    const match = new RegExp(`(?<![\\w-])${pattern}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in species-entry.css`)
    return match[1]
}

/** Extracts the contents of the first brace-balanced block whose opening
 *  brace follows `startPattern` -- same technique as `theme.css.test.ts`'s
 *  `extractBlock`, needed here for the `@supports` block below, which
 *  contains nested rules a naive `[^}]*` regex would stop at the first of. */
function extractBlock(source: string, startPattern: RegExp): string {
    const startMatch = startPattern.exec(source)
    if (!startMatch) throw new Error(`No match for ${startPattern} in species-entry.css`)
    const braceStart = source.indexOf("{", startMatch.index)
    if (braceStart === -1) throw new Error(`No opening brace after match for ${startPattern}`)
    let depth = 0
    for (let i = braceStart; i < source.length; i++) {
        if (source[i] === "{") depth++
        else if (source[i] === "}") {
            depth--
            if (depth === 0) return source.slice(braceStart + 1, i)
        }
    }
    throw new Error(`Unbalanced braces after match for ${startPattern}`)
}

describe("conformer card grid: three per row, degrading at narrower widths", () => {
    it("declares three fixed columns by default -- not auto-fit, which let the count float", () => {
        const rule = extractRule(css, ".conformer-list")
        expect(rule).toMatch(/grid-template-columns:\s*repeat\(3,\s*minmax\(17rem,\s*1fr\)\)/)
        expect(rule).not.toMatch(/auto-fit/)
    })

    it("steps down to two columns at a narrower breakpoint", () => {
        const block = extractBlock(css, /@media \(max-width: 1180px\) \{/)
        expect(block).toMatch(/\.conformer-list\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(17rem,\s*1fr\)\)/)
    })

    it("collapses to a single column at the existing narrow breakpoint", () => {
        const block = extractBlock(css, /@media \(max-width: 680px\) \{/)
        expect(block).toMatch(/\.conformer-list\s*\{[^}]*grid-template-columns:\s*1fr/)
    })
})

describe("conformer card meta/coverage: one line, ellipsis rather than wrap or silent truncation", () => {
    const rule = extractRule(css, ".conformer-card-meta, .conformer-card-coverage")

    it("never wraps", () => {
        expect(rule).toMatch(/white-space:\s*nowrap/)
    })

    it("clips visibly (ellipsis), rather than silently, when it cannot fit", () => {
        expect(rule).toMatch(/overflow:\s*hidden/)
        expect(rule).toMatch(/text-overflow:\s*ellipsis/)
    })

    it("allows the ellipsis to actually apply by zeroing the grid/flex item's implied minimum width", () => {
        // Without this, a grid/flex item's automatic minimum width is its
        // content's width, which would push the card wider instead of
        // clipping -- the ellipsis rule above would be dead code without it.
        expect(rule).toMatch(/min-width:\s*0/)
    })
})

describe("conformer basin identity: bordered box, top and bottom, same shape whichever variant renders", () => {
    it(".conformer-basin-identity has a rule above AND below", () => {
        const rule = extractRule(css, ".conformer-basin-identity")
        expect(rule).toMatch(/border-top:\s*1px solid var\(--line-2\)/)
        expect(rule).toMatch(/border-bottom:\s*1px solid var\(--line-2\)/)
    })

    it(".conformer-basin-rigid (the sibling \"no rotatable bonds\" variant) gets the identical treatment", () => {
        const rule = extractRule(css, ".conformer-basin-rigid")
        expect(rule).toMatch(/border-top:\s*1px solid var\(--line-2\)/)
        expect(rule).toMatch(/border-bottom:\s*1px solid var\(--line-2\)/)
    })
})

describe("conformer card row-track sharing (subgrid, with a fallback)", () => {
    // A browser without subgrid support must still render something
    // functional -- the base (non-`@supports`) rules are exactly the
    // pre-existing flex-column layout, unconditionally in effect, and the
    // subgrid behaviour only ever applies as an ADDITIVE override inside
    // `@supports (grid-template-rows: subgrid)`.
    it("the fallback (unconditional) card rule is a plain flex column, not a grid -- the degrade path", () => {
        const rule = extractRule(css, ".conformer-card")
        expect(rule).toMatch(/display:\s*flex/)
        expect(rule).not.toMatch(/display:\s*grid/)
    })

    const supportsBlock = extractBlock(css, /@supports \(grid-template-rows: subgrid\) \{/)

    it("feature-detects subgrid support before using it", () => {
        expect(css).toMatch(/@supports \(grid-template-rows: subgrid\)/)
    })

    it("the card becomes a subgrid over 5 shared row tracks once subgrid is supported", () => {
        const cardRule = extractRule(supportsBlock, ".conformer-card")
        expect(cardRule).toMatch(/display:\s*grid/)
        expect(cardRule).toMatch(/grid-template-rows:\s*subgrid/)
        expect(cardRule).toMatch(/grid-row:\s*span 5/)
    })

    it("the select button nests its own subgrid over the label/meta/coverage rows, so a taller label never nudges another card's meta out of line", () => {
        const buttonRule = extractRule(supportsBlock, ".conformer-card > .conformer-card-select")
        expect(buttonRule).toMatch(/display:\s*grid/)
        expect(buttonRule).toMatch(/grid-template-rows:\s*subgrid/)
        expect(buttonRule).toMatch(/grid-row:\s*1 \/ span 3/)
    })

    // This is the assertion that catches the real bug: EVERY row below the
    // button is an explicit line number, not left to auto-placement. A
    // card that renders no basin element at all (the archive's own
    // majority case -- a null fingerprint renders neither
    // `.conformer-basin-identity` nor `.conformer-basin-rigid`) would
    // otherwise auto-place its very next sibling, the references
    // disclosure, one row EARLIER than a sibling card that did render a
    // basin box -- exactly the per-card desynchronisation this pass
    // exists to remove. Pinning both to fixed lines means a 1-rotor card
    // and a 7-rotor card -- and a 0-rotor (no element at all) card --
    // all place their references disclosure on the SAME shared row.
    it("pins the basin element (either variant) to row 4 by an explicit line number, not auto-placement", () => {
        const basinRule = extractRule(
            supportsBlock,
            ".conformer-card > .conformer-basin-identity, .conformer-card > .conformer-basin-rigid",
        )
        expect(basinRule).toMatch(/grid-row:\s*4/)
    })

    it("pins the references disclosure to row 5 by an explicit line number, regardless of whether row 4 rendered anything", () => {
        const refsRule = extractRule(supportsBlock, ".conformer-card > .refs-disclosure")
        expect(refsRule).toMatch(/grid-row:\s*5/)
    })

    it("gives the list a 5-track repeating row pattern so every card, in every visual row of cards, gets the same 5 shared tracks", () => {
        const listRule = extractRule(supportsBlock, ".conformer-list")
        expect(listRule).toMatch(/grid-auto-rows:\s*auto auto auto auto auto/)
    })
})

describe("References disclosure border removal is scoped to the conformer card, not global", () => {
    // The owner's complaint was about the conformer card specifically; the
    // SAME `RefsDisclosure` component also renders in `.entry-hero` (this
    // page's top-of-page identifiers block), which was never part of that
    // complaint. A global deletion of the base rule would pass a
    // conformer-only assertion -- both directions are asserted here so a
    // global deletion cannot slip through unnoticed.
    it("the base (hero-applicable) rule still draws the border when open", () => {
        const rule = extractRule(css, ".refs-disclosure[open] summary")
        expect(rule).toMatch(/border-bottom:\s*1px solid var\(--line-2\)/)
    })

    it("a conformer-card-scoped override removes it, at higher specificity than the base rule", () => {
        const rule = extractRule(css, ".conformer-card .refs-disclosure[open] summary")
        expect(rule).toMatch(/border-bottom:\s*none/)
    })
})
