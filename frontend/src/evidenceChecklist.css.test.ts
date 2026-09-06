import { describe, expect, it } from "vitest"

// DISCOVERED, never enumerated -- same technique `value-pill-scope.css.test.ts`/
// `design-system.css.test.ts` each use for their own "exactly one declaration"
// guards: an explicit file list is a guard pointed at a fixed set of targets,
// and a new stylesheet absent from that list would be unexamined, not
// passing, which is indistinguishable from the outside. `import.meta.glob`
// enumerates what actually exists under `src/**/*.css` instead -- recursive
// (post-review, review of 2bd17511: the sibling guards above only glob
// `./*.css`, since every stylesheet they care about is flat under `src/`
// today; this one globs `./**/*.css` so a future component-owned sheet
// placed under a subdirectory, e.g. `components/`, is still covered).
const STYLESHEET_SOURCES = import.meta.glob("./**/*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>
const ALL_STYLESHEETS: Record<string, string> = Object.fromEntries(
    Object.entries(STYLESHEET_SOURCES).map(([path, css]) => [path.replace(/^\.\//, ""), css]),
)

/** Strips `/* ... *\/` block comments so a comment mentioning a retired
 *  class name in prose (explaining why it's gone) can never be mistaken
 *  for a live declaration -- same helper `value-pill-scope.css.test.ts`/
 *  `design-system.css.test.ts`/`theme.css.test.ts` each carry their own
 *  copy of, for the same reason. */
function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

/**
 * Post-review fix (review of 2bd17511): the FIRST version of this guard
 * only matched a selector immediately followed by `{` at the start of its
 * own line (`^\s*SELECTOR\s*\{`) -- MEASURED, that pattern let every one
 * of these through as a "declares no rule" false pass: a page-scoped
 * descendant selector (`.geometry-page .coverage-checklist {}`), a
 * bespoke nested override (`.coverage-checklist dt {}`), and a bare
 * duplicate declared anywhere BUT the start of its own line (e.g. two
 * selectors on one line, `.foo, .coverage-checklist {}`). Any of those is
 * a real second declaration of the class this guard exists to keep to
 * one file -- the class name appearing ANYWHERE in a selector list, not
 * just as a lone bare selector, is what matters. `selectorPattern` below
 * requires the class name be followed by a character that can legally
 * follow a class name in a real CSS selector (whitespace, `,`, `.`, `:`,
 * `{`, `[`, or a combinator) -- i.e. it matches the class name wherever it
 * appears as a real selector token, the same technique
 * `value-pill-scope.css.test.ts`'s own `VALUE_PILL_SELECTOR` uses.
 */
function selectorPattern(className: string): RegExp {
    const escaped = className.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    return new RegExp(`${escaped}(?=[\\s,.:{[>+~])`)
}

/** Every stylesheet (name, comment-stripped source) that declares `className`
 *  as a real selector token anywhere. */
function declaredIn(className: string): string[] {
    const pattern = selectorPattern(className)
    return Object.entries(ALL_STYLESHEETS)
        .filter(([, css]) => pattern.test(stripComments(css)))
        .map(([name]) => name)
}

/**
 * `.coverage-card`/`.coverage-checklist` are `EvidenceChecklist.tsx`'s own
 * classes -- declared in exactly one place, its own stylesheet,
 * `evidence-checklist.css` (the repo's "a component owns its own CSS"
 * convention: `RecordIdentityHeader` -> `record-identity-header.css`,
 * `RefsDisclosure` -> `refs-disclosure.css`, `EnergyDisplay` -> `energy-
 * display.css`). `.ledger-summary--single` is a `.ledger-summary` variant
 * used directly in page markup (not a class the component itself
 * renders), so it stays in `conformer-group.css` next to `.ledger-
 * summary`. `.validation-card` (the geometry page's former bespoke,
 * non-`--derived` evidence-box class) is retired outright -- declared
 * nowhere.
 */
describe("evidence checklist card: each selector has exactly one CSS home (or none, for the retired one)", () => {
    it(".coverage-card is declared only in evidence-checklist.css", () => {
        expect(declaredIn(".coverage-card")).toEqual(["evidence-checklist.css"])
    })

    it(".coverage-checklist is declared only in evidence-checklist.css", () => {
        expect(declaredIn(".coverage-checklist")).toEqual(["evidence-checklist.css"])
    })

    it(".ledger-summary--single is declared only in conformer-group.css", () => {
        expect(declaredIn(".ledger-summary--single")).toEqual(["conformer-group.css"])
    })

    it(".validation-card is declared nowhere -- retired outright", () => {
        expect(declaredIn(".validation-card")).toEqual([])
    })
})

/**
 * Item 1 fix (review of 2bd17511): retiring the old `.coverage-card span
 * { display: block }` (which used to style BOTH the heading span and,
 * via a sibling `.coverage-card strong` rule, the pre-`EvidenceChecklist`
 * inline value) also un-blocked the `<span class="t-label">` HEADING --
 * un-scoped, a bare `<span>` computes `display: inline` by default.
 * MEASURED: the heading's own box lost ~4px, and the `<dl>` below it (a
 * block sibling, unaffected in itself) sat 4px higher for it -- the whole
 * card shrunk by that much on all four pages. `.coverage-card > .t-label`
 * (direct-child, scoped to exactly the heading this component renders)
 * is the fix.
 */
describe(".coverage-card > .t-label heading is display: block (item 1, post-review of 2bd17511)", () => {
    it("evidence-checklist.css declares .coverage-card > .t-label { display: block }", () => {
        const css = stripComments(ALL_STYLESHEETS["evidence-checklist.css"] ?? "")
        const match = /\.coverage-card\s*>\s*\.t-label\s*\{([^}]*)\}/.exec(css)
        expect(match, "no .coverage-card > .t-label rule found in evidence-checklist.css").not.toBeNull()
        expect(match![1]).toMatch(/display:\s*block/)
    })
})

// Mutation check: the FIRST version of this guard (`^\s*SELECTOR\s*\{`)
// would have let every one of these through undetected -- each is a real
// second declaration of `.coverage-checklist`, just not shaped as a bare
// `.coverage-checklist { ... }` rule sitting alone at the start of its own
// line. Proven directly against the pattern (not by actually mutating a
// real file) -- same technique `value-pill-scope.css.test.ts`'s own
// mutation check uses.
describe("mutation check: the selector-anywhere pattern catches what a bare-declaration check misses", () => {
    const pattern = selectorPattern(".coverage-checklist")

    it("catches a page-scoped descendant selector", () => {
        expect(pattern.test(".geometry-page .coverage-checklist{}")).toBe(true)
    })

    it("catches a bespoke nested override", () => {
        expect(pattern.test(".coverage-checklist dt{}")).toBe(true)
    })

    it("catches a bare duplicate declared mid-line, not at the start of its own line", () => {
        expect(pattern.test("   .coverage-checklist {\n    margin: 0;\n}")).toBe(true)
        expect(pattern.test(".some-other-rule {} .coverage-checklist {}")).toBe(true)
    })

    it("catches the class inside a comma-separated selector list", () => {
        expect(pattern.test(".foo, .coverage-checklist { color: red; }")).toBe(true)
    })

    it("sanity: a longer, unrelated class name is never mistaken for it", () => {
        expect(pattern.test(".coverage-checklist-extra { color: red; }")).toBe(false)
    })
})
