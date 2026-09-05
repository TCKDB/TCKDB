import { describe, expect, it } from "vitest"

/**
 * SHOULD-FIX-15 (species-entry/browse/chrome residuals re-review): four
 * stylesheets this PR's own brief named as carrying dead classes --
 * `species-entry.css`, `entry-science.css`, `browse.css`, `index.css` --
 * plus everything else the same re-review's retirement list named
 * (`.evidence-chip`, `.empty-stage`, `.geometry-list`, `.identifier-chip*`)
 * were already gone or out of this PR's file scope by the time this test
 * was written (grepped: `.evidence-chip` was already comment-only,
 * `.empty-stage`/`.geometry-list` live in `conformer-group.css`, PR E's
 * file). What THIS test guards is the general defect, not just the named
 * instances: every class selector in these four files must have at least
 * one real `.tsx` consumer (not a test, not a comment), so a future class
 * that stops being rendered gets caught here instead of accumulating the
 * same way `.browse-row-ts-label` did (found and removed by this same PR
 * -- zero consumers, not even in a test).
 *
 * DISCOVERED, never enumerated -- same `import.meta.glob` technique
 * `design-system.css.test.ts`/`value-pill-scope.css.test.ts` already use
 * for the stylesheet side; applied here to the `.tsx` consumer side too,
 * so a new component file is picked up automatically rather than needing
 * this test's own file list maintained by hand.
 */
const STYLESHEET_SOURCES = import.meta.glob("./*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>
const ALL_STYLESHEETS: Record<string, string> = Object.fromEntries(
    Object.entries(STYLESHEET_SOURCES).map(([path, css]) => [path.replace(/^\.\//, ""), css]),
)

const CHECKED_STYLESHEETS = ["species-entry.css", "entry-science.css", "browse.css", "index.css"]

// Every `.tsx` source in the project, EXCLUDING test files -- a class
// referenced only by a `*.test.tsx` assertion (`document.querySelector`,
// say) is not actually rendered by any real page, so it would not count
// as a live consumer here.
const COMPONENT_SOURCES = import.meta.glob("./**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>

function stripCssComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

/** Strips both `/* ... *\/` block comments and `// ...` line comments from
 *  TSX source -- review finding (post-approval): a class name mentioned
 *  only inside a `.tsx` COMMENT (e.g. a docstring citing the class it
 *  used to be, or a "TODO: reach for .foo here" note) used to count as a
 *  live consumer, since the component side was never comment-stripped the
 *  way the stylesheet side already is. Line-comment stripping is a
 *  pragmatic heuristic (not a full JS tokenizer -- it does not understand
 *  string/template literals or regex literals), guarded against the one
 *  false-positive case this codebase's own comments are full of: a `//`
 *  inside a URL (`https://...`) is never treated as a comment opener,
 *  since it is always preceded by `:`. */
function stripJsComments(source: string): string {
    const noBlockComments = source.replace(/\/\*[\s\S]*?\*\//g, "")
    return noBlockComments.replace(/(^|[^:])\/\/.*$/gm, "$1")
}

const COMPONENT_TEXT = Object.entries(COMPONENT_SOURCES)
    .filter(([path]) => !path.includes(".test."))
    .map(([, source]) => stripJsComments(source))
    .join("\n")

/** Every `.class-name` token appearing anywhere in the (comment-stripped)
 *  stylesheet text -- selectors, not just top-of-rule ones, so a
 *  descendant/compound selector (`.foo .bar`, `.foo.bar`) still surfaces
 *  both names. Filenames inside `url(...)`/`format(...)` strings (e.g.
 *  `.woff2`) also match this pattern (a literal dot followed by letters)
 *  without being a class at all -- `NOT_A_CLASS` below is the explicit
 *  allowlist for exactly that case, not a loophole for a genuinely dead
 *  class. */
function classNamesIn(css: string): Set<string> {
    const stripped = stripCssComments(css)
    return new Set(Array.from(stripped.matchAll(/\.([A-Za-z_][A-Za-z0-9_-]*)/g), (match) => match[1]))
}

/** True if `className` appears anywhere in the component source text as
 *  its own token -- not as a substring of a longer identifier (so
 *  checking `"card"` does not count a hit inside `"card--selected"`). */
function hasComponentConsumer(className: string): boolean {
    const pattern = new RegExp(`(?<![A-Za-z0-9_-])${className.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![A-Za-z0-9_-])`)
    return pattern.test(COMPONENT_TEXT)
}

// Explicit allowlist, with a REASON for each entry -- per the brief
// ("allow an explicit allowlist with reasons"). A `Set` here (the
// original shape) let an entry get added with no reason at all, silently
// -- review finding (post-approval): "the allowlist grows silently".
// `Map<className, reason>` makes a reason-less addition a type error, and
// the tests below additionally enforce that the reason actually SAYS
// something (>= 20 chars, not just a placeholder) and that the token it
// excuses is real -- present in one of the checked stylesheets, so a
// stale entry (the class it names was since deleted outright) fails
// instead of silently accumulating unused exceptions forever.
const NOT_A_CLASS = new Map<string, string>([
    // `@font-face { src: url("....woff2") format("woff2") }` -- a file
    // extension / format-string literal inside `index.css`, matched by
    // this test's `\.[A-Za-z_]...` pattern the same way a real class
    // selector is (a literal dot followed by letters), but it is never a
    // CSS class.
    ["woff2", "a @font-face format-string literal in index.css (src: url(...) format(\"woff2\")), not a class selector at all"],
])

describe("every class selector in species-entry/entry-science/browse/index.css has a real .tsx consumer", () => {
    it("discovers the four checked stylesheets among what actually exists", () => {
        for (const name of CHECKED_STYLESHEETS) {
            expect(Object.keys(ALL_STYLESHEETS)).toContain(name)
        }
    })

    it.each(CHECKED_STYLESHEETS)("%s: every class selector has at least one .tsx consumer (or a documented allowlist reason)", (stylesheetName) => {
        const css = ALL_STYLESHEETS[stylesheetName]
        const classNames = classNamesIn(css)
        expect(classNames.size).toBeGreaterThan(0)
        const deadClasses = Array.from(classNames)
            .filter((name) => !NOT_A_CLASS.has(name))
            .filter((name) => !hasComponentConsumer(name))
        expect(deadClasses).toEqual([])
    })
})

describe("NOT_A_CLASS allowlist entries are reasoned and current, not silent escape hatches", () => {
    it("every allowlisted token carries a real reason, not a placeholder (>= 20 characters)", () => {
        for (const [token, reason] of NOT_A_CLASS) {
            expect(reason.length, `NOT_A_CLASS["${token}"]'s reason is too short to be a real explanation: "${reason}"`).toBeGreaterThanOrEqual(20)
        }
    })

    it("every allowlisted token actually occurs in one of the checked stylesheets -- a stale entry (the class it names was since deleted) fails here", () => {
        const allCheckedText = CHECKED_STYLESHEETS.map((name) => ALL_STYLESHEETS[name]).join("\n")
        for (const token of NOT_A_CLASS.keys()) {
            const pattern = new RegExp(`(?<![A-Za-z0-9_-])${token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![A-Za-z0-9_-])`)
            expect(pattern.test(allCheckedText), `NOT_A_CLASS["${token}"] does not occur in any checked stylesheet -- stale entry, remove it`).toBe(true)
        }
    })
})
