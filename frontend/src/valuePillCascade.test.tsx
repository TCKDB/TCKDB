import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
// Real (non-`?raw`) imports, in the SAME order `main.tsx` loads them --
// `css: true` in this project's vitest config makes Vite actually process
// and inject these as real stylesheets into jsdom (unlike a `?raw` import,
// which only hands back source text -- see the comment atop
// `geometry-detail.css.test.ts`), so `getComputedStyle` below reflects the
// real cascade a browser would compute, not a source-text guess at it.
import "./theme.css"
import "./index.css"
// `?raw` sources, for the static ordering/specificity assertions below --
// see the comment ahead of those for why they, not a rendered jsdom check,
// are what actually pins the specificity half of the fix.
import designSystemCssRaw from "./design-system.css?raw"
import indexCssRaw from "./index.css?raw"
import disclosureSource from "./components/Disclosure.tsx?raw"

afterEach(cleanup)

/**
 * Regression coverage for a real bug a code review caught in this PR:
 * `.value-pill.value-pill--muted` (the "not reviewed" pill on the species
 * overview and both browse rows) computed the BASE pill's accent-50/
 * accent-700 colours instead of its own muted surface-sunken/muted ones,
 * depending on navigation order -- measured to render correctly on a fresh
 * load of `/species/<ref>` (where `Disclosure.tsx` had already injected a
 * second copy of `design-system.css` into that route's own chunk) but
 * WRONG on `/species?kind=species` loaded directly (no such second copy,
 * so `index.css`'s own `.value-pill` -- loaded after `design-system.css`'s
 * `@import`, equal specificity -- won the cascade).
 *
 * Two independent, class-name-blind checks below: a REAL rendered
 * `getComputedStyle` read (which a `.toHaveClass("value-pill--muted")`
 * assertion cannot see -- that assertion is exactly what shipped this bug,
 * since the class was always present, only its WINNING declaration was
 * wrong), and a source-order/specificity check on the raw CSS. Either one
 * alone would have caught the shipped bug; both are kept because they
 * guard against different regressions (a future reorder vs. a future
 * duplicate-injection).
 */
describe("`.value-pill.value-pill--muted` actually renders muted, not the base pill's colour", () => {
    it("in real production import order (theme.css, then index.css -- design-system.css comes in via index.css's own @import)", () => {
        const { container } = render(
            <div>
                <span className="value-pill" id="base">classification</span>
                <span className="value-pill value-pill--muted" id="muted">not reviewed</span>
            </div>,
        )
        const base = container.querySelector("#base") as HTMLElement
        const muted = container.querySelector("#muted") as HTMLElement

        // jsdom's CSS engine does not resolve `var()` to a hex/rgb value --
        // MEASURED: `getComputedStyle` hands back the winning declaration's
        // raw text (`"var(--accent-700)"`, `"var(--muted)"`), not a
        // resolved colour. That raw text is still exactly what this bug
        // needs: `.value-pill` and `.value-pill--muted` reference DIFFERENT
        // custom properties for `color`, so which raw string wins tells you
        // which RULE won the cascade -- the actual question under test.
        expect(getComputedStyle(base).color).toBe("var(--accent-700)")
        expect(getComputedStyle(muted).color).toBe("var(--muted)")
        expect(getComputedStyle(muted).color).not.toBe(getComputedStyle(base).color)
    })

    // A rendered check for the SPECIFICITY defense (fix 2) specifically --
    // "does `.value-pill.value-pill--muted` keep winning even against a
    // bare `.value-pill` rule injected later" -- was tried here and
    // dropped: MEASURED, jsdom's `getComputedStyle` does not weigh selector
    // specificity across separate `<style>` elements at all, only source
    // order (confirmed by swapping which rule loads last and watching
    // jsdom's answer flip every time, contradicting real CSS's specificity
    // rule, which never yields to order). A test asserting jsdom's answer
    // here would not be evidence about the real browser cascade this fix
    // targets -- it would only be evidence about jsdom's own simplified
    // engine, and could fail (or pass) independent of whether the fix is
    // correct. The two static source-level checks below (selector
    // specificity, declaration order) are what actually pin fix 2 and fix
    // 1 respectively; they read the real CSS text rather than asking an
    // engine that does not implement the rule under test to grade its own
    // homework.
})

describe("source-level guards for the fix (belt-and-suspenders with the rendered check above)", () => {
    it("design-system.css declares .value-pill BEFORE .value-pill.value-pill--muted, in the same file", () => {
        const baseIndex = designSystemCssRaw.indexOf(".value-pill {")
        const mutedIndex = designSystemCssRaw.indexOf(".value-pill.value-pill--muted {")
        expect(baseIndex).toBeGreaterThanOrEqual(0)
        expect(mutedIndex).toBeGreaterThanOrEqual(0)
        expect(baseIndex).toBeLessThan(mutedIndex)
    })

    it("the muted variant's selector carries higher specificity than a bare .value-pill--muted (two classes, not one)", () => {
        expect(designSystemCssRaw).toMatch(/\.value-pill\.value-pill--muted\s*\{/)
        // Every mention of `.value-pill--muted` as a rule's own selector
        // (immediately followed by `{`, ignoring the doc comments that also
        // name it) must be part of the two-class compound selector above --
        // a bare, single-class `.value-pill--muted { ... }` rule would be
        // equal-specificity with `.value-pill` again, back to depending on
        // source order alone, which is exactly what broke here twice.
        const ruleSelectors = [...designSystemCssRaw.matchAll(/([.\w-]+(?:\.[.\w-]+)*)\s*\{/g)].map((m) => m[1])
        const mutedSelectors = ruleSelectors.filter((selector) => selector.includes("value-pill--muted"))
        expect(mutedSelectors).toEqual([".value-pill.value-pill--muted"])
    })

    it("index.css no longer defines .value-pill itself -- design-system.css is the one place both rules live", () => {
        expect(indexCssRaw).not.toMatch(/\.value-pill\s*\{/)
    })

    it("Disclosure.tsx no longer imports design-system.css itself -- index.css already does, once, globally", () => {
        // Anchored to the START of a (trimmed) line so this only matches a
        // real import STATEMENT, not the explanatory comment directly above
        // `DisclosureProps` that mentions the same quoted string as history.
        expect(disclosureSource).not.toMatch(/^\s*import\s+["']\.\.\/design-system\.css["']/m)
    })
})
