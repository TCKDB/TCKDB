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
