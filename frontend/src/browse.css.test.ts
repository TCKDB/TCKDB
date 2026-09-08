import { describe, expect, it } from "vitest"
// `?raw` loads the file as plain source text, not a processed stylesheet --
// see the comment atop `geometry-detail.css.test.ts` for why this suffix
// (not a bare `./browse.css` import) is required under this project's
// `css: true` vitest config, and why `node:fs` is not an option for a file
// under `src/` (no `"node"` entry in `tsconfig.app.json`'s `types`).
import css from "./browse.css?raw"

/** Extracts the declaration block for a single, non-nested selector, e.g.
 *  `extractRule(css, ".browse-header h1")` returns everything between
 *  `.browse-header h1 {` and its matching `}`. Throws if the selector isn't
 *  found, so a rename that forgets to update this file fails loudly rather
 *  than silently matching nothing. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector "${selector}" in browse.css`)
    return match[1]
}

// PR D (design-system adoption on the index/record pages): the browse page
// used to define its own 57.6px/700 h1, its own row box style, an 11px
// faint ref, and duplicate label/hint/pagination typography rather than
// pointing at the shared type scale. Every rule below pins one of those
// migrations so a later edit that quietly reverts one fails here instead of
// shipping unnoticed.
describe(".browse-header h1 -- the shared display-2 step", () => {
    it("uses var(--type-display-2-font), not its own literal clamp", () => {
        const rule = extractRule(css, ".browse-header h1")
        expect(rule).toMatch(/font:\s*var\(--type-display-2-font\)/)
        expect(rule).not.toMatch(/font-size:\s*clamp/)
    })

    it("no longer carries the 4px accent left-bar on .browse-header", () => {
        const rule = extractRule(css, ".browse-header")
        expect(rule).not.toMatch(/border-left/)
    })
})

describe(".browse-row -- box styling comes from the shared .card primitive", () => {
    it("no longer declares its own padding/border/border-radius/background", () => {
        // `.browse-row` used to be its own rule with a box style; it is now
        // only ever mentioned in a comment (both row components add `.card`
        // alongside it in `SpeciesBrowseRow.tsx`/`TransitionStateBrowseRow.tsx`).
        // A real `.browse-row {` rule reappearing here would mean the box
        // style regressed back to a page-local definition.
        expect(css).not.toMatch(/\.browse-row\s*\{/)
    })
})

describe(".browse-row-title -- heading-2", () => {
    it("uses var(--type-heading-2-font), not its own literal size", () => {
        const rule = extractRule(css, ".browse-row-title")
        expect(rule).toMatch(/font:\s*var\(--type-heading-2-font\)/)
        expect(rule).not.toMatch(/font:\s*600 1\.15rem/)
    })
})

describe(".browse-row-provenance -- --type-value", () => {
    it("uses var(--type-value-font)", () => {
        const rule = extractRule(css, ".browse-row-provenance")
        expect(rule).toMatch(/font:\s*var\(--type-value-font\)/)
    })
})

describe(".browse-row-evidence -- --type-note", () => {
    it("is its own rule (no longer grouped with .browse-row-smiles/.browse-row-ts-label) and uses var(--type-note-font)", () => {
        const rule = extractRule(css, ".browse-row-evidence")
        expect(rule).toMatch(/font:\s*var\(--type-note-font\)/)
    })

    it(".browse-row-smiles/.browse-row-ts-label no longer share a rule with .browse-row-evidence", () => {
        expect(css).not.toMatch(/\.browse-row-smiles,\s*\n?\s*\.browse-row-ts-label,\s*\n?\s*\.browse-row-evidence/)
    })
})

describe("the ref is a data run, not an 11px faint one-off", () => {
    it(".browse-ref declares no font of its own -- typography comes from the .data class applied alongside it", () => {
        const rule = extractRule(css, ".browse-ref")
        expect(rule).not.toMatch(/font:/)
        expect(rule).not.toMatch(/font-size:/)
    })

    it("the old .browse-row-ref selector no longer has a rule of its own (a comment may still name it for history)", () => {
        expect(css).not.toMatch(/\.browse-row-ref\s*\{/)
    })
})

describe("filter form typography", () => {
    it(".browse-filter-field label uses the shared --type-label step", () => {
        const rule = extractRule(css, ".browse-filter-field label")
        expect(rule).toMatch(/font:\s*var\(--type-label-font\)/)
    })

    it(".browse-filter-evidence-group legend uses the shared --type-label step", () => {
        const rule = extractRule(css, ".browse-filter-evidence-group legend")
        expect(rule).toMatch(/font:\s*var\(--type-label-font\)/)
    })

    it(".browse-filter-field select uses the shared --type-ui step", () => {
        const rule = extractRule(css, ".browse-filter-field select")
        expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
    })

    it(".browse-filter-field input uses the shared --type-value step", () => {
        const rule = extractRule(css, ".browse-filter-field input")
        expect(rule).toMatch(/font:\s*var\(--type-value-font\)/)
    })

    it(".browse-filter-hint is capped at --measure-note", () => {
        const rule = extractRule(css, ".browse-filter-hint")
        expect(rule).toMatch(/max-width:\s*var\(--measure-note\)/)
    })
})

describe(".browse-pagination button -- --type-ui", () => {
    it("uses var(--type-ui-font), not its own literal size", () => {
        const rule = extractRule(css, ".browse-pagination button")
        expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
    })
})

describe("no off-token divider colour", () => {
    it("--divider is no longer referenced in this file", () => {
        expect(css).not.toMatch(/var\(--divider\)/)
    })
})

// Post-review (PR D) fix:
describe(".browse-count -- the shared --type-label step, not a hand-rolled .72rem/.04em pair", () => {
    it("uses var(--type-label-font)", () => {
        const rule = extractRule(css, ".browse-count")
        expect(rule).toMatch(/font:\s*var\(--type-label-font\)/)
        expect(rule).not.toMatch(/font:\s*\.72rem/)
    })
})

// ---------------------------------------------------------------------------
// SHOULD-FIX-11 (species-entry/browse/chrome residuals re-review): the
// browse page carried its own bespoke sizes -- `.76rem`/`.78rem`/`.84rem`/
// `.86rem`/`.92rem`, none matching a named type-scale step -- across the
// row meta line, the kind selector, the filter checkboxes, and the status/
// empty lines. Each maps onto a named step now.
// ---------------------------------------------------------------------------
describe(".browse-row-meta -- --type-note, not a bare mono .76rem", () => {
    it("uses var(--type-note-font)", () => {
        const rule = extractRule(css, ".browse-row-meta")
        expect(rule).toMatch(/font:\s*var\(--type-note-font\)/)
        expect(rule).not.toMatch(/var\(--mono\)/)
    })
})

describe(".browse-row-smiles -- --type-data (mono), consolidated from two split rules", () => {
    it("uses var(--type-data-font), and the class is declared exactly once", () => {
        const rule = extractRule(css, ".browse-row-smiles")
        expect(rule).toMatch(/font:\s*var\(--type-data-font\)/)
        const occurrences = css.match(/\.browse-row-smiles\s*\{/g) ?? []
        expect(occurrences).toHaveLength(1)
    })
})

describe("filter checkbox labels -- --type-value, not a bare .84rem", () => {
    it(".browse-filter-field-check label uses var(--type-value-font)", () => {
        const rule = extractRule(css, ".browse-filter-field-check label")
        expect(rule).toMatch(/font:\s*var\(--type-value-font\)/)
    })

    it(".browse-filter-evidence-check uses var(--type-value-font)", () => {
        const rule = extractRule(css, ".browse-filter-evidence-check")
        expect(rule).toMatch(/font:\s*var\(--type-value-font\)/)
    })
})

describe("status/empty lines -- --type-note, not a bare .92rem", () => {
    it(".browse-status uses var(--type-note-font)", () => {
        const rule = extractRule(css, ".browse-status")
        expect(rule).toMatch(/font:\s*var\(--type-note-font\)/)
    })

    it(".browse-empty uses var(--type-note-font)", () => {
        const rule = extractRule(css, ".browse-empty")
        expect(rule).toMatch(/font:\s*var\(--type-note-font\)/)
    })
})

// Review follow-up (PR 4b, round 2): the reaction browse row RE-CREATED the
// stereo-chip defect `reaction-entry.css.test.ts` already guards for the
// entry/chooser pages (`.record-identity-title .reaction-equation-chip` /
// `.record-identity-title code.data`) -- that fix's selector never reaches
// this row, and `reaction-entry.css` is not even in the browse page's CSS
// chunk. MEASURED before this fix (real Chrome): the chip and the formula
// beside it both rendered at the row title's own 20px `--type-heading-2-
// font` step, ratio 1.00, in accent color with a permanent underline
// (`.card a`) -- an English aside ("· S enantiomer") reading as equally
// prominent as the chemistry, live on 4+ of the first 20 unfiltered rows.
//
// This is a SOURCE-TEXT regex against the stylesheet, the same technique
// `reaction-entry.css.test.ts` uses for the identical bug, not a
// `getComputedStyle` pixel assertion -- confirmed empirically (see
// `ReactionBrowseRow.test.tsx`'s own comment on the equivalent rendered
// test) that this project's jsdom/cssstyle version does not resolve
// `var(...)` custom properties in EITHER shorthand or plain longhand
// declarations, so a computed-pixel assertion here would silently pass
// against a broken rule as readily as a fixed one. The real, rendered
// ratio (13px chip / 20px title, both themes, 1920 and 680) is verified in
// an actual browser as part of the PR's manual verification pass.
describe("reaction browse row -- stereo-chip capped and muted, not a 1:1 inherited size", () => {
    it(".reaction-browse-row-title .reaction-equation-chip is capped to --type-note-font and muted, not the title's own heading step", () => {
        const rule = extractRule(css, ".reaction-browse-row-title .reaction-equation-chip")
        expect(rule).toMatch(/font:\s*var\(--type-note-font\)/)
        expect(rule).toMatch(/color:\s*var\(--muted\)/)
        // Never the title's own step -- the exact regression this guards:
        // someone "fixing" a lint complaint by pointing the chip back at
        // the same token the title uses.
        expect(rule).not.toMatch(/--type-heading-2-font/)
    })

    it(".reaction-browse-row-title code.data inherits the surrounding font-size, not its own fixed --type-data-font (companion fix, mirrors reaction-entry.css)", () => {
        const rule = extractRule(css, ".reaction-browse-row-title code.data")
        expect(rule).toMatch(/font-size:\s*inherit/)
    })
})

// Review follow-up: the ENTIRE point of this PR -- making the reaction
// browse card one click target for the reaction entry -- lives in a single
// CSS rule, `.reaction-browse-row .browse-row-title::after`. jsdom does
// not compute pseudo-element styles at all (confirmed: `getComputedStyle`
// on a `::after` selector returns nothing meaningful in this project's
// jsdom/cssstyle version), so `ReactionBrowseRow.test.tsx`'s own
// computed-style suite cannot see this rule -- it can only assert that the
// row and footer ancestors are positioned. Without a SOURCE-TEXT assertion
// here, that one line could be deleted entirely and the whole rendered
// test suite would stay green while the on-page click target collapsed
// from the full card down to a sliver around the equation text (MEASURED:
// deleting this rule drops on-target click points from 2940/2940 to
// 170/2940 in a real browser, with 57 unrelated tests still passing).
// Same source-text technique the stereo-chip suite above uses, for the
// same reason: no computed-pixel assertion can catch this class of
// regression here.
describe("reaction browse row -- title-link stretched-click overlay (the one rule that makes the whole card a single click target)", () => {
    it(".reaction-browse-row .browse-row-title::after is an absolutely positioned, full-box (inset: 0) empty overlay", () => {
        const rule = extractRule(css, ".reaction-browse-row .browse-row-title::after")
        expect(rule).toMatch(/content:\s*""/)
        expect(rule).toMatch(/position:\s*absolute/)
        expect(rule).toMatch(/inset:\s*0/)
    })

    it(".reaction-browse-row itself is positioned, so the overlay's inset:0 resolves against the ROW (the whole card), not the title text alone", () => {
        const rule = extractRule(css, ".reaction-browse-row")
        expect(rule).toMatch(/position:\s*relative/)
    })
})
