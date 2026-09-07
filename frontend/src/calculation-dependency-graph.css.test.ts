import { describe, expect, it } from "vitest"
// `?raw` = plain source text, not the processed stylesheet -- see
// `geometry-detail.css.test.ts` for why (`css: true` in `vite.config.ts`
// would otherwise inject the stylesheet instead of handing back its
// source).
import css from "./calculation-dependency-graph.css?raw"

/**
 * "Colours and type from design-system tokens only (`--accent`, `--line`,
 * `--ink`, `--muted`, the type scale); no hard-coded hex" -- the brief's
 * own constraint on this file. jsdom cannot see whether a colour LOOKS
 * right (no real paint), but a hex literal in the raw source is a
 * mechanical fact this test can catch without a browser: any `#rgb` /
 * `#rrggbb` / `#rgba` / `#rrggbbaa` literal anywhere in the file is a
 * token this stylesheet should have referenced with `var(--...)` instead.
 */
describe("calculation-dependency-graph.css declares no hex colour literal", () => {
    it("contains no # hex literal anywhere in the file", () => {
        expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    })

    it("contains no rgb()/rgba()/hsl()/hsla() literal anywhere in the file", () => {
        expect(css).not.toMatch(/\b(rgb|rgba|hsl|hsla)\s*\(/i)
    })
})

/** Every `fill`/`stroke`/`color`/`background` declaration in this file
 * resolves through a `var(--...)` custom property, never a bare CSS
 * colour keyword (`black`, `steelblue`, ...) either -- the same "tokens
 * only" rule, checked against the actual declarations rather than just
 * the absence of `#`. */
describe("every colour-bearing declaration in this file uses var(--...)", () => {
    const COLOR_PROPERTIES = ["fill", "stroke", "color", "background", "background-color", "outline"]

    it("never sets a colour property to a bare keyword or literal", () => {
        // `none` turns the property off entirely (no paint at all) rather
        // than setting an actual colour, so it carries no token to check --
        // `fill: none` (an edge path's own fill) and `outline: none` (the
        // link-focus reset, overridden by the very next rule) are both
        // this, not an escaped hex/keyword.
        const declarationLines = css
            .split("\n")
            .map((line) => line.trim())
            .filter((line) => COLOR_PROPERTIES.some((prop) => line.startsWith(`${prop}:`)) && !line.endsWith(": none;"))
        expect(declarationLines.length).toBeGreaterThan(0)
        for (const line of declarationLines) {
            expect(line).toMatch(/var\(--[a-z0-9-]+\)/)
        }
    })
})

describe("the centre node reuses .card--selected's own accent treatment", () => {
    it("fills with --accent-50 and strokes with --accent, like design-system.css's .card--selected", () => {
        const match = /\.dep-graph-node--centre \.dep-graph-node-rect\s*\{([^}]*)\}/.exec(css)
        expect(match, ".dep-graph-node--centre .dep-graph-node-rect rule not found").not.toBeNull()
        expect(match![1]).toMatch(/fill:\s*var\(--accent-50\)/)
        expect(match![1]).toMatch(/stroke:\s*var\(--accent\)/)
    })
})

describe("the centre node's type pill is a real pill, not bare text", () => {
    it(".dep-graph-node-pill-bg fills --accent-50 and strokes --accent-300 (visible against the centre node's own --accent-50 fill)", () => {
        const match = /\.dep-graph-node-pill-bg\s*\{([^}]*)\}/.exec(css)
        expect(match, ".dep-graph-node-pill-bg rule not found").not.toBeNull()
        expect(match![1]).toMatch(/fill:\s*var\(--accent-50\)/)
        expect(match![1]).toMatch(/stroke:\s*var\(--accent-300\)/)
    })
})

describe("the SVG scales fluidly and never forces page-level horizontal overflow", () => {
    it(".dep-graph-svg is display:block with width:100% and height:auto", () => {
        const match = /\.dep-graph-svg\s*\{([^}]*)\}/.exec(css)
        expect(match, ".dep-graph-svg rule not found").not.toBeNull()
        expect(match![1]).toMatch(/display:\s*block/)
        expect(match![1]).toMatch(/width:\s*100%/)
        expect(match![1]).toMatch(/height:\s*auto/)
    })
})

describe("the demoted sentence list uses the .note step, not body prose", () => {
    it(".dep-graph-sentences uses --type-note-font and --muted", () => {
        const match = /\.dep-graph-sentences\s*\{([^}]*)\}/.exec(css)
        expect(match, ".dep-graph-sentences rule not found").not.toBeNull()
        expect(match![1]).toMatch(/font:\s*var\(--type-note-font\)/)
        expect(match![1]).toMatch(/color:\s*var\(--muted\)/)
    })

    // The rule this replaces (`.dependency-sentences li`, retired from
    // `calculation-detail.css` -- see that file's own test) capped a
    // sentence row to `--measure-note`, the "readable prose measure"
    // step, since each row is a genuine sentence, not raw data. Pinned
    // here in its new home so the cap itself survives the move.
    it(".dep-graph-sentences caps to --measure-note, carrying forward calculation-detail.css's retired SHOULD-FIX-4 rule", () => {
        const match = /\.dep-graph-sentences\s*\{([^}]*)\}/.exec(css)
        expect(match, ".dep-graph-sentences rule not found").not.toBeNull()
        expect(match![1]).toMatch(/max-width:\s*var\(--measure-note\)/)
    })
})
