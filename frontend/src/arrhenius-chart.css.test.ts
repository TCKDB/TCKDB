import { describe, expect, it } from "vitest"
// `?raw` loads the file as plain source text, not a processed stylesheet --
// see the comment atop `geometry-detail.css.test.ts` for why this suffix
// (not a bare `./arrhenius-chart.css` import) is required under this
// project's `css: true` vitest config, and why `node:fs` is not an option
// for a file under `src/` (no `"node"` entry in `tsconfig.app.json`'s
// `types`).
import css from "./arrhenius-chart.css?raw"

/** Extracts the declaration block for a single, non-nested selector, e.g.
 *  `extractRule(css, ".arrhenius-chart-svg")` returns everything between
 *  `.arrhenius-chart-svg {` and its matching `}`. Throws if the selector
 *  isn't found, so a rename that forgets to update this file fails loudly
 *  rather than silently matching nothing. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector "${selector}" in arrhenius-chart.css`)
    return match[1]
}

// The tokens-only rule itself (no hex/rgb literal anywhere in this file,
// every var(--x) defined on :root) is already enforced GLOBALLY, for every
// stylesheet under src/ including this one, by `theme.css.test.ts`'s
// `import.meta.glob("./*.css", ...)` discovery -- see that file's own
// "DISCOVERED, never enumerated" comment. This file only needs to check
// the two defects specific to THIS chart's own spec (plan §4/PR 3): the
// SVG is pinned to a fixed pixel size rather than scaled by its container,
// and the x-axis title is pinned to the chart's own width rather than
// centred in a wider grid column.
describe("arrhenius-chart.css uses var(--token) only (also covered globally by theme.css.test.ts)", () => {
    it("contains no hex colour literal", () => {
        expect(css.match(/#[0-9a-fA-F]{3,8}\b/g)).toBeNull()
    })

    // Three-way rgb()/rgba()/hsl()/hsla() check -- the same pattern
    // `calculation-dependency-graph.css.test.ts` uses. The previous version
    // of this test only matched `rgba?\(\s*\d`, which would have said
    // nothing about an `hsl(...)`/`hsla(...)` literal landing in this file.
    it("contains no rgb()/rgba()/hsl()/hsla() literal", () => {
        expect(css).not.toMatch(/\b(rgb|rgba|hsl|hsla)\s*\(/i)
    })
})

// ---------------------------------------------------------------------------
// Defect (b) from this PR's own brief: the design-review mock's SVG scaled
// down with its container (`width: 100%; max-width: 720px`, the SAME rule
// `thermo-cp-chart.css`'s `.cp-chart-svg` still carries), which at a 680px
// viewport pushed the viewBox-driven tick-label text below the 11.52px
// accessibility floor. `.arrhenius-chart-svg` here must set NEITHER a
// percentage width NOR a `max-width` -- the component sizes the element with
// real `width`/`height` SVG attributes instead, and this rule only adds
// `display: block`.
// ---------------------------------------------------------------------------
describe(".arrhenius-chart-svg -- fixed pixel size, never a percentage/max-width that would shrink its text", () => {
    it("carries no width or max-width rule of its own (sized via SVG attributes at the call site instead)", () => {
        const rule = extractRule(css, ".arrhenius-chart-svg")
        expect(rule).not.toMatch(/(?<!min-)(?<!\w)width\s*:/)
        expect(rule).not.toMatch(/max-width\s*:/)
    })
})

describe(".arrhenius-chart-scroll -- contains the fixed-width SVG without forcing the page wider", () => {
    it("scrolls its own overflow horizontally, rather than letting a narrow viewport shrink the chart", () => {
        const rule = extractRule(css, ".arrhenius-chart-scroll")
        expect(rule).toMatch(/overflow-x\s*:\s*auto/)
    })
})

// The mock's OTHER round-2 defect: the x-axis title was centred in its grid
// COLUMN (which can be wider than the plotted chart on a wide viewport),
// rather than under the chart itself. `.arrhenius-chart-axis-title--x`
// itself only centres text within whatever box it's given; the box's own
// offset/width is pinned to the PLOT box (not the full SVG box, whose own
// left/right margins are asymmetric -- a later review finding, ~19px off)
// at the call site (`style={{ marginLeft: left, width: plotWidth }}`,
// `ArrheniusChart.tsx`), and sits inside the SAME `.arrhenius-chart-scroll`
// container as the SVG so the two always scroll together and stay aligned.
describe(".arrhenius-chart-axis-title--x -- pinned to the PLOT box, not a wider grid column or the full SVG box", () => {
    it("is placed inside .arrhenius-chart-scroll in the component (co-located with the SVG), not a separate always-full-width grid cell", () => {
        // `.arrhenius-chart-axis-title--x` itself carries no grid-column
        // override in this stylesheet -- unlike `.cp-chart-axis-title--x`
        // (thermo-cp-chart.css), which explicitly re-parents itself into
        // `grid-column: 2` of the panel grid (a WIDER track than the SVG
        // can be on a narrow viewport). Its offset/width instead comes
        // from the inline style at the call site, matching the plotted
        // curve's own horizontal box exactly (not the full SVG box).
        const rule = extractRule(css, ".arrhenius-chart-axis-title--x")
        expect(rule).not.toMatch(/grid-column/)
        expect(rule).not.toMatch(/(?<!margin-)(?<!\w)width\s*:/)
        expect(rule).toMatch(/text-align\s*:\s*center/)
    })
})
