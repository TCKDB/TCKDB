import { describe, expect, it } from "vitest"
import stylesheet from "./reaction-entry.css?raw"

/**
 * Regression guard for the h1-typography review finding: a SMILES fallback
 * (`code.data`, `--type-data-font` 13px mono) rendered illegibly tiny
 * inside the `--type-display-2` (36px) equation h1, and a served stereo
 * label (`.reaction-equation-chip`) rendered the OPPOSITE way -- uncapped,
 * it inherited the full 36px and became the single most prominent text on
 * the chooser page. See `ReactionEquation.tsx`'s own docstring for the
 * MEASURED reports both rules below fix.
 */
describe("reaction-entry.css: equation h1 typography fixes", () => {
    it("code.data inside the identity title inherits the surrounding font-size (not the fixed --type-data-font)", () => {
        expect(stylesheet).toMatch(/\.record-identity-title\s+code\.data\s*\{[^}]*font-size:\s*inherit/)
    })

    it(".reaction-equation-chip is capped to --type-body-font, not the h1's own display size", () => {
        expect(stylesheet).toMatch(/\.record-identity-title\s+\.reaction-equation-chip\s*\{[^}]*font:\s*var\(--type-body-font\)/)
    })
})

/**
 * Round-2 review finding: `.data-table th` (design-system.css) uppercases
 * every header via `--type-label-strong-transform` -- correct for an
 * ordinary column name, but renders the k(T) table's own "k (cm³ mol⁻¹
 * s⁻¹)" header as "K (CM³ MOL⁻¹ S⁻¹)", indistinguishable from the adjacent
 * "T (K)" column. The override must be SCOPED to `.kinetics-k-table th`
 * specifically -- a bare `th { text-transform: none }` (or any other
 * unscoped variant) would silently un-uppercase every OTHER data-table
 * header on the site, which this test also guards against.
 */
describe("reaction-entry.css: k(T) table header is not uppercased, and the fix is scoped", () => {
    it("declares .kinetics-k-table th { text-transform: none }", () => {
        expect(stylesheet).toMatch(/\.kinetics-k-table\s+th\s*\{[^}]*text-transform:\s*none/)
    })

    it("never declares a bare/unscoped `th { text-transform: none }` that would leak to every data-table", () => {
        // Every text-transform:none declaration in this file must be
        // preceded by the `.kinetics-k-table` scope -- not just `th`, not
        // `.data-table th` (which would strip the transform from EVERY
        // data-table on the site, not just this one).
        const stripped = stylesheet.replace(/\/\*[\s\S]*?\*\//g, "")
        const rules = stripped.match(/[^{}]+\{[^}]*text-transform:\s*none[^}]*\}/g) ?? []
        expect(rules.length).toBeGreaterThan(0)
        for (const rule of rules) {
            const selector = rule.slice(0, rule.indexOf("{")).trim()
            expect(selector).toBe(".kinetics-k-table th")
        }
    })
})
