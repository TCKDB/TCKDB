import { describe, expect, it } from "vitest"
// `?raw` loads the stylesheet as plain source text, unprocessed -- see the
// comment atop `geometry-detail.css.test.ts` for why this suffix (not a
// bare import) is required under this project's `css: true` vitest config.
import css from "./molecule-page.css?raw"

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
    if (!match) throw new Error(`No rule found for selector ${selector} in molecule-page.css`)
    return match[1]
}

/** Extracts the contents of the first brace-balanced block whose opening
 *  brace follows `startPattern` -- same technique as `theme.css.test.ts`'s
 *  `extractBlock`, needed here for the `@supports` block below, which
 *  contains nested rules a naive `[^}]*` regex would stop at the first of. */
function extractBlock(source: string, startPattern: RegExp): string {
    const startMatch = startPattern.exec(source)
    if (!startMatch) throw new Error(`No match for ${startPattern} in molecule-page.css`)
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

describe("shape cards: a list of cards that shrink to fit", () => {
    it("is three columns on a wide screen, built from minmax(0, 1fr) so a card never forces the page wider", () => {
        expect(extractRule(css, ".conformer-list")).toMatch(/grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/)
    })

    it("steps down to two columns at 1500px and one on a phone", () => {
        expect(extractBlock(css, /@media \(max-width: 1500px\) \{/)).toMatch(/repeat\(2,\s*minmax\(0,\s*1fr\)\)/)
        expect(extractBlock(css, /@media \(max-width: 680px\) \{/)).toMatch(/\.conformer-list\s*\{[^}]*grid-template-columns:\s*1fr/)
    })

    it("lets a card shrink and wraps long refs inside the energy and usage lines", () => {
        expect(extractRule(css, ".conformer-card")).toMatch(/min-width:\s*0/)
        expect(extractRule(css, ".shape-card-energy, .shape-usedby")).toMatch(/overflow-wrap:\s*anywhere/)
    })

    it("never clips the counts line", () => {
        expect(extractRule(css, ".conformer-card-meta")).not.toMatch(/text-overflow/)
    })

    it("marks the selected card with the accent border (the button also carries aria-pressed)", () => {
        expect(extractRule(css, '.conformer-card[data-selected="true"]')).toMatch(/border-color:\s*var\(--accent\)/)
    })

    it("lays the cards out three to a row inside the shared page width", () => {
        expect(extractRule(css, ".conformer-list")).toMatch(/grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\)/)
    })

    it("gives each card's 3D link the house link style, and a 44px target on phones and touch screens", () => {
        expect(css).toMatch(/\.shape-card-3d a \{\s*color: var\(--accent-700\);\s*text-decoration: underline;/)
        expect(css).toMatch(/@media \(max-width: 62rem\), \(pointer: coarse\) \{\s*\.shape-card-3d a \{\s*min-height:\s*2\.75rem/)
    })

    it("defines no rule for a removed selector", () => {
        const code = css.replace(/\/\*[\s\S]*?\*\//g, "")
        for (const gone of [".conformer-basin-identity", ".conformer-basin-rigid", ".conformer-card-coverage", "subgrid"]) {
            expect(code).not.toContain(gone)
        }
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
        ".conformer-card-meta",
        ".shape-card-energy, .shape-usedby",
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

describe("calculations table and page-wide guards", () => {
    const code = css.replace(/\/\*[\s\S]*?\*\//g, "")

    it("keeps the deposit column readable: header cells never break mid-word, the row header has a floor, cells may wrap anywhere", () => {
        expect(extractRule(css, ".shape-calculations-table thead th")).toMatch(/overflow-wrap:\s*normal/)
        expect(extractRule(css, ".shape-calculations-table thead th")).toMatch(/white-space:\s*nowrap/)
        expect(extractRule(css, ".shape-calculations-table tbody th")).toMatch(/min-width:\s*8rem/)
        expect(extractRule(css, ".shape-calculations-table tbody th")).not.toMatch(/(^|[\s;])width:/)
        expect(css).toMatch(/\.shape-calculations-table td \{ overflow-wrap: anywhere; \}/)
    })

    it("stacks the rows on a phone", () => {
        const phone = extractBlock(css, /@media \(max-width: 680px\) \{/)
        expect(phone).toMatch(/\.shape-calculations-table th,[^{]*\{[^}]*display:\s*block/)
    })

    it("scopes the hidden-text utility to this page instead of defining a global class", () => {
        expect(code).not.toMatch(/(^|[\s,}])\.visually-hidden\b/)
        expect(code).toMatch(/\.molecule-page-visually-hidden\s*\{/)
    })

    it("never clips text with an ellipsis", () => {
        expect(code).not.toMatch(/text-overflow\s*:/)
    })

    it("never pairs overflow: hidden with white-space: nowrap, except in the clip utility that hides text visually", () => {
        for (const match of code.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
            const selector = match[1].trim()
            if (selector.includes("molecule-page-visually-hidden")) continue
            expect(/overflow:\s*hidden/.test(match[2]) && /white-space:\s*nowrap/.test(match[2]), selector).toBe(false)
        }
    })
})
