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

    it(".browse-kind-selector legend uses the shared --type-label step", () => {
        const rule = extractRule(css, ".browse-kind-selector legend")
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
