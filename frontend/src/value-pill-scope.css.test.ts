import { describe, expect, it } from "vitest"

// DISCOVERED, never enumerated -- same technique `design-system.css.test.ts`
// uses for its own "exactly one bare .table-scroll rule" guard: an explicit
// file list is a guard pointed at a fixed set of targets, and a new
// stylesheet absent from that list would be unexamined, not passing, which
// is indistinguishable from the outside. `import.meta.glob` enumerates what
// actually exists under `src/*.css` instead.
const STYLESHEET_SOURCES = import.meta.glob("./*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>
const ALL_STYLESHEETS: Record<string, string> = Object.fromEntries(
    Object.entries(STYLESHEET_SOURCES).map(([path, css]) => [path.replace(/^\.\//, ""), css]),
)

/** Strips `/* ... *\/` block comments so a comment mentioning a class name
 *  in prose can never be mistaken for a live declaration -- same helper
 *  `design-system.css.test.ts`/`theme.css.test.ts` each carry their own
 *  copy of, for the same reason. */
function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

/**
 * PR D review finding (design/foundations "record pages" consolidation,
 * PR B): `transition-state-entry.css` used to carry `.tse-siblings-list
 * .value-pill { font-size: .68rem }` -- a descendant selector that reaches
 * into the shared `.value-pill` primitive (`index.css`) directly, restyling
 * it wherever this page's own markup happens to nest a pill, rather than
 * composing a new modifier class the way `.value-pill--muted` does. Vite
 * never unloads a route's own CSS chunk once it has been injected (the
 * exact "whichever page loaded last wins the cascade for every route after
 * it" hazard `entry-science.css`'s own file header names for `.kv-list`),
 * so ANY page stylesheet doing this is a live risk, not a hypothetical one
 * -- not because a descendant selector reaches other pages' pills directly
 * (it does not; `.value-pill` alone still wins there), but because it is
 * exactly the pattern that turns into a real leak the moment a future edit
 * drops the ancestor scope, widens it, or the same selector text gets
 * copied into a differently-scoped rule. The fix: a page needing a smaller
 * pill defines its own modifier class (composed ALONGSIDE `.value-pill`/
 * `.value-pill--muted` in the markup, e.g. `.tse-sibling-pill`) whose own
 * selector never ends in `.value-pill` at all, so a reader (or this test)
 * never has to reason about descendant-selector scope to know it is safe.
 *
 * `design-system.css` and `index.css` are exempt: `.value-pill` and
 * `.value-pill--muted` are DEFINED there -- that is the primitive itself,
 * not a page restyling it.
 */
describe("no page stylesheet restyles the shared .value-pill primitive directly", () => {
    it("only design-system.css/index.css declare a .value-pill-suffixed rule", () => {
        const offenders: string[] = []
        for (const [name, css] of Object.entries(ALL_STYLESHEETS)) {
            if (name === "design-system.css" || name === "index.css") continue
            if (/(?<![\w.-])\.value-pill(?:--muted)?\s*\{/.test(stripComments(css))) offenders.push(name)
        }
        expect(offenders).toEqual([])
    })

    it("transition-state-entry.css no longer overrides .value-pill's font-size via a descendant selector", () => {
        const css = stripComments(ALL_STYLESHEETS["transition-state-entry.css"] ?? "")
        expect(css).not.toMatch(/\.value-pill/)
    })
})
