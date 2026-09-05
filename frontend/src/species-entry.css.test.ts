import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text, unprocessed -- see the
// comment atop `geometry-detail.css.test.ts` for why this suffix (not a
// bare import) is required under this project's `css: true` vitest config.
import css from "./species-entry.css?raw"
// Base `.refs-disclosure` rules moved out of this file into their own
// stylesheet (imported by `components/RefsDisclosure.tsx` itself, so
// every consumer -- not just `SpeciesEntryPage` -- gets them). Only the
// species-page-specific overrides (`.conformer-card`-scoped) stay in
// `species-entry.css`; see the last `describe` block below.
import refsDisclosureCss from "./refs-disclosure.css?raw"

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
    it("declares three fixed columns by default, floored at 22rem -- not auto-fit (which let the count float), and low enough that three columns actually hold on a 1920px screen", () => {
        const rule = extractRule(css, ".conformer-list")
        expect(rule).toMatch(/grid-template-columns:\s*repeat\(3,\s*minmax\(22rem,\s*1fr\)\)/)
        expect(rule).not.toMatch(/auto-fit/)
        expect(rule).not.toMatch(/17rem/)
    })

    it("steps down to two columns at 1500px -- below that three 22rem columns no longer fit", () => {
        const block = extractBlock(css, /@media \(max-width: 1500px\) \{/)
        expect(block).toMatch(/\.conformer-list\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(22rem,\s*1fr\)\)/)
    })

    it("collapses to a single column at the existing narrow breakpoint", () => {
        const block = extractBlock(css, /@media \(max-width: 680px\) \{/)
        expect(block).toMatch(/\.conformer-list\s*\{[^}]*grid-template-columns:\s*1fr/)
    })
})

describe("entry page cap widened to fit three full-width cards (item 2)", () => {
    it(".entry-page's max-width is at least 123.5rem -- 3 * 22rem cards + 2 * 1rem gaps + 14rem ToC rail + 3rem ToC gap + 2.5rem page padding, the exact budget three unclipped cards need", () => {
        const rule = extractRule(css, ".entry-page")
        const match = /max-width:\s*([\d.]+)rem/.exec(rule)
        expect(match, "no max-width declared on .entry-page").not.toBeNull()
        expect(Number(match![1])).toBeGreaterThanOrEqual(123.5)
    })
})

describe("conformer card meta/coverage: one line, no ellipsis or silent clip (item 2)", () => {
    // "when I said do not wrap I did not say do ellipsis for texts that
    // go[es] longer than the boxes. the boxes need to be longer in
    // width" -- the fix is `.conformer-list`'s 22rem floor above, sized
    // to the longest measured real line (69 characters); this rule must
    // never clip again, silently or with an ellipsis.
    const rule = extractRule(css, ".conformer-card-meta, .conformer-card-coverage")

    it("never sets text-overflow: ellipsis", () => {
        expect(rule).not.toMatch(/text-overflow/)
    })

    it("never combines overflow: hidden with white-space: nowrap -- the pairing that produces a silent or visible clip", () => {
        const hidesOverflow = /overflow:\s*hidden/.test(rule)
        const forcesNoWrap = /white-space:\s*nowrap/.test(rule)
        expect(hidesOverflow && forcesNoWrap).toBe(false)
    })
})

describe("conformer basin identity: exactly ONE separator per boundary, not two (item 4)", () => {
    // The owner: "I am seeing two faint grey line breaks before the
    // REFERENCE section... and no faint grey line above the basins
    // section". Cause: `.conformer-basin-identity`/`-rigid` used to carry
    // BOTH border-top and border-bottom, so the basin's own border-bottom
    // stacked directly against `.refs-disclosure`'s border-top -- two
    // lines. Fix: the basin box keeps ONLY its border-top (the boundary
    // with the card-select button above it); the boundary with
    // References below is `.refs-disclosure`'s own border-top alone, one
    // rule, not two -- checked below on both the identity and rigid
    // variants, since either can render into the same slot.
    it(".conformer-basin-identity has a border ABOVE only, never below", () => {
        const rule = extractRule(css, ".conformer-basin-identity")
        expect(rule).toMatch(/border-top:\s*1px solid var\(--line-2\)/)
        expect(rule).not.toMatch(/border-bottom/)
    })

    it(".conformer-basin-rigid (the sibling \"no rotatable bonds\" variant) gets the identical top-only treatment", () => {
        const rule = extractRule(css, ".conformer-basin-rigid")
        expect(rule).toMatch(/border-top:\s*1px solid var\(--line-2\)/)
        expect(rule).not.toMatch(/border-bottom/)
    })

    it(".conformer-card .refs-disclosure supplies the ONE separator before References, using the SAME token as the basin box (--line-2, not --line) -- deliberately one shade, not two", () => {
        const rule = extractRule(css, ".conformer-card .refs-disclosure")
        expect(rule).toMatch(/border-top:\s*1px solid var\(--line-2\)/)
        expect(rule).not.toMatch(/border-bottom/)
    })

    // The third card shape (neither basin variant renders -- the
    // archive's own majority case, a null fingerprint) needs no CSS rule
    // of its own: `.conformer-card .refs-disclosure`'s border-top above
    // is unconditional on the CSS side (it does not depend on a basin
    // element existing), so the same single rule is what separates
    // References from the card-select button directly when nothing else
    // sits between them. `ConformerSelector.test.tsx`'s DOM-structure
    // tests pin the three actual DOM shapes (basin-identity, basin-rigid,
    // neither) this reasoning depends on.
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
    //
    // The base rule itself now lives in `refs-disclosure.css` (moved out
    // of this file so every page rendering `RefsDisclosure` gets it, not
    // just this one) -- read from there rather than `species-entry.css`.
    it("the base (hero-applicable) rule still draws the border when open", () => {
        const rule = extractRule(refsDisclosureCss, ".refs-disclosure[open] summary")
        expect(rule).toMatch(/border-bottom:\s*1px solid var\(--line-2\)/)
    })

    it("a conformer-card-scoped override removes it, at higher specificity than the base rule", () => {
        const rule = extractRule(css, ".conformer-card .refs-disclosure[open] summary")
        expect(rule).toMatch(/border-bottom:\s*none/)
    })
})

// ---------------------------------------------------------------------------
// SHOULD-FIX-5 (species-entry/browse/chrome residuals re-review): the
// linkage connector arrow used to be `align-self: center`, vertically
// centred against the WHOLE 350px-tall column -- against a short tile it
// hung ~150px below the number it points away from (MEASURED). It also
// only hid/rotated below the 880px VIEWPORT breakpoint, which never fired
// at 1100px where the ToC rail alone narrows the row below the ~836px
// three steps need -- MEASURED, the third tile wraps with no arrow there.
// ---------------------------------------------------------------------------
describe("linkage connector alignment and wrap-safe hiding", () => {
    it(".linkage-flow is a named inline-size container, so a container query can key off its own rendered width rather than the viewport's", () => {
        const rule = extractRule(css, ".linkage-flow")
        expect(rule).toMatch(/container-type:\s*inline-size/)
        expect(rule).toMatch(/container-name:\s*linkage-flow/)
    })

    it(".linkage-connector aligns to the start of its column with a top margin, not centered against the whole column", () => {
        const rule = extractRule(css, ".linkage-connector")
        expect(rule).toMatch(/align-self:\s*start/)
        expect(rule).not.toMatch(/align-self:\s*center/)
        // Pinned to the actual value (not just "some value") -- `\S+`
        // alone would have accepted a regression to `margin-top: 0`,
        // which is exactly `align-self: center`'s own effective
        // top-offset on this element (no explicit margin there at all)
        // and would have silently un-done the baseline-approximating fix.
        expect(rule).toMatch(/margin-top:\s*\.55rem/)
    })

    it("hides the connector via a container query scoped to widths ABOVE the 880px column breakpoint, so it never suppresses the intentional rotated arrow in column layout", () => {
        const gate = extractBlock(css, /@media \(min-width: 881px\) \{/)
        expect(gate).toMatch(/@container linkage-flow \(max-width: 836px\)/)
        const hideRule = extractRule(gate, ".linkage-connector")
        expect(hideRule).toMatch(/display:\s*none/)
    })

    it("the 880px-and-below column layout keeps its own rotated, always-visible connector, untouched by the new hide rule", () => {
        const columnBlock = extractBlock(css, /@media \(max-width: 880px\) \{/)
        const rule = extractRule(columnBlock, ".linkage-connector")
        expect(rule).toMatch(/rotate\(90deg\)/)
        expect(rule).not.toMatch(/display:\s*none/)
    })
})

// ---------------------------------------------------------------------------
// SHOULD-FIX-3 (species-entry/browse/chrome residuals re-review): MEASURED,
// the entry/statmech/thermo tabs ran 51-68% mono by visible character
// count -- these rules were most of it: full prose sentences and metric
// summaries set in monospace, the face this house's own type scale
// reserves for an identifier or number. Locks each one onto a named sans
// step (never `var(--mono)`) so a future edit cannot silently revert it.
// ---------------------------------------------------------------------------
describe("mono is reserved for identifiers and numbers, not prose (item 3)", () => {
    it.each([
        ".conformer-card-meta, .conformer-card-coverage",
        ".conformer-basin-rigid",
        ".linkage-step-detail",
        ".linkage-geometry-list",
        ".conformer-attribution-answer",
        ".conformer-attribution-other .conformer-evidence-group-heading",
    ])("%s: a prose/summary rule never sets a mono font", (selector) => {
        const rule = extractRule(css, selector)
        expect(rule).not.toMatch(/var\(--mono\)/)
    })

    // The geometry ref INSIDE the (now sans) list item is the one thing in
    // that list that must stay mono -- it is an identifier, not prose.
    it(".linkage-geometry-list code stays mono -- it renders a geometry ref, an identifier", () => {
        const rule = extractRule(css, ".linkage-geometry-list code")
        expect(rule).toMatch(/var\(--type-data-font\)/)
    })

    // The step count is a NUMBER ("18", "7", "11") -- `--type-data-large`
    // is mono deliberately; this is the one rule in this group that
    // should NOT lose its mono face, pinned so a future "make everything
    // sans" pass does not overcorrect it.
    it(".linkage-step-count stays mono via --type-data-large -- it renders a number, not prose", () => {
        const rule = extractRule(css, ".linkage-step-count")
        expect(rule).toMatch(/var\(--type-data-large-font\)/)
    })
})
