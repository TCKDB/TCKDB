import { describe, expect, it } from "vitest"
// `?raw` loads the file as plain source text, not a processed stylesheet --
// see the comment atop `geometry-detail.css.test.ts` for why this suffix
// (not a bare `./thermo-cp-chart.css` import) is required under this
// project's `css: true` vitest config, and why `node:fs` is not an option
// for a file under `src/` (no `"node"` entry in `tsconfig.app.json`'s
// `types`).
import css from "./thermo-cp-chart.css?raw"

/** Extracts the declaration block for a single, non-nested selector, e.g.
 *  `extractRule(css, ".cp-chart-legend-item")` returns everything between
 *  `.cp-chart-legend-item {` and its matching `}`. Throws if the selector
 *  isn't found, so a rename that forgets to update this file fails loudly
 *  rather than silently matching nothing. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector "${selector}" in thermo-cp-chart.css`)
    return match[1]
}

// ---------------------------------------------------------------------------
// SHOULD-FIX-12 (species-entry/browse/chrome residuals re-review): the Cp
// chart carried three more bespoke sizes -- `.74rem`/`.64rem`/`.68rem`,
// the last two below the site's own 11px accessibility floor and `.64rem`
// (10.24px) the smallest text anywhere on the site, MEASURED. Each maps
// onto a named type-scale step now.
// ---------------------------------------------------------------------------
describe(".cp-chart-legend-item -- --type-data (mono, no case transform)", () => {
    it("uses var(--type-data-font), not a bare .74rem", () => {
        const rule = extractRule(css, ".cp-chart-legend-item")
        expect(rule).toMatch(/font:\s*var\(--type-data-font\)/)
        expect(rule).not.toMatch(/font:\s*\.74rem/)
    })

    // The legend text is `groupLegendLabel`'s own mixed-case phrase and can
    // embed a real ref in parentheses ("Conformer Group 1 (thm_a1)") --
    // this must never force it upper-case the way `--type-label` would.
    it("never forces a text-transform (a ref can sit inside this text)", () => {
        const rule = extractRule(css, ".cp-chart-legend-item")
        expect(rule).not.toMatch(/text-transform/)
    })
})

describe(".cp-chart-legend-flag -- --type-label-strong, not the smallest text on the site", () => {
    it("uses var(--type-label-strong-font), not a bare .64rem", () => {
        const rule = extractRule(css, ".cp-chart-legend-flag")
        expect(rule).toMatch(/font:\s*var\(--type-label-strong-font\)/)
        expect(rule).not.toMatch(/font-size:\s*\.64rem/)
    })
})

describe(".cp-chart-axis-title -- --type-label, not a sub-floor .68rem", () => {
    it("uses var(--type-label-font), not a bare .68rem", () => {
        const rule = extractRule(css, ".cp-chart-axis-title")
        expect(rule).toMatch(/font:\s*var\(--type-label-font\)/)
        expect(rule).not.toMatch(/font-size:\s*\.68rem/)
    })
})

describe(".cp-chart-tick-label -- unchanged, already --type-data (regression guard)", () => {
    it("still uses var(--type-data-font)", () => {
        const rule = extractRule(css, ".cp-chart-tick-label")
        expect(rule).toMatch(/font:\s*var\(--type-data-font\)/)
    })
})
