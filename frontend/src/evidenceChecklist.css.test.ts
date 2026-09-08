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
 * `.coverage-checklist` is `EvidenceChecklist.tsx`'s own class -- declared
 * in exactly one place, its own stylesheet, `evidence-checklist.css` (the
 * repo's "a component owns its own CSS" convention: `RecordIdentityHeader`
 * -> `record-identity-header.css`, `RefsDisclosure` -> `refs-disclosure.
 * css`, `EnergyDisplay` -> `energy-display.css`). `.ledger-summary--single`
 * is a `.ledger-summary` variant used directly in page markup (not a
 * class the component itself renders), so it stays in `conformer-
 * group.css` next to `.ledger-summary`. `.validation-card` (the geometry
 * page's former bespoke, non-`--derived` evidence-box class) is retired
 * outright -- declared nowhere.
 *
 * `.coverage-card` (RETIRED as a styled selector, independent review, this
 * branch): it used to carry exactly one rule, `.coverage-card > .t-label
 * { display: block }` -- see the comment below this describe block for
 * why that rule is gone. `.coverage-card` itself gets its box chrome
 * entirely from the shared `.card`/`.card--derived` classes
 * (design-system.css); it has never had a bare `.coverage-card { ... }`
 * rule of its own, so with the direct-child rule gone it is declared
 * NOWHERE now, the same as `.validation-card` below -- it survives only
 * as a marker class (`EvidenceChecklist.tsx`'s own JSX + `evidence
 * checklist.css.test.ts`'s `data-component` marker, and every page-level
 * test's `.card.card--derived.coverage-card` query hook).
 */
describe("evidence checklist card: each selector has exactly one CSS home (or none, for the retired one)", () => {
    it(".coverage-card is declared nowhere -- retired as a styled selector, survives only as a marker class", () => {
        expect(declaredIn(".coverage-card")).toEqual([])
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

// The `.coverage-card > .t-label { display: block }` rule this file used
// to pin here is RETIRED (independent review, this branch): the heading
// moved inside `Disclosure`'s own `<summary>` when the card became
// collapsible, so that selector is no longer a direct-child path and
// matches nothing rendered -- a test pinning its SOURCE TEXT would go red
// the moment someone deletes the (now dead) rule, without the rendered
// page ever changing, which is a substring guard, not a behaviour guard.
// The rule is genuinely unnecessary now, not replaced by anything: the
// heading and the `<dl>` it used to protect the spacing of are no longer
// adjacent siblings at all (heading in `<summary>`, `<dl>` in a separate
// `.disclosure-body`), so the heading's own `display` (still plain
// `inline`, MEASURED -- nothing blockifies it) can no longer affect the
// `<dl>`'s position. Pinned as a DOM/structure assertion in
// `EvidenceChecklist.test.tsx` instead (heading and checklist proven to
// sit in different disclosure regions), where a regression actually
// means something rendered differently.

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
