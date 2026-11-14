import { describe, expect, it } from "vitest"

/**
 * Guards the general defect of a dead class: every class selector in every
 * stylesheet except `UNCHECKED_STYLESHEETS` must have at least
 * one real `.ts` or `.tsx` consumer (not a test, not a comment), so a future class
 * that stops being rendered gets caught here instead of accumulating the
 * same way `.browse-row-ts-label` did (zero consumers, not even in a test).
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

/** Stylesheets left out of the check, each with the reason. */
const UNCHECKED_STYLESHEETS = new Map<string, string>([
    ["theme.css", "tokens only; it defines no class selectors"],
])

/** Classes in `design-system.css` that are the pinned shared vocabulary, used or not: every `.t-*` type utility and these modifiers. */
const DESIGN_SYSTEM_VOCABULARY = new Set(["card--sunken", "card--selected", "data--select"])
const isDesignSystemVocabulary = (name: string) => /^t-/.test(name) || DESIGN_SYSTEM_VOCABULARY.has(name)

const CHECKED_STYLESHEETS = Object.keys(ALL_STYLESHEETS).filter((name) => !UNCHECKED_STYLESHEETS.has(name)).sort()

// Every `.tsx` source in the project, EXCLUDING test files -- a class
// referenced only by a `*.test.tsx` assertion (`document.querySelector`,
// say) is not actually rendered by any real page, so it would not count
// as a live consumer here.
const COMPONENT_SOURCES = import.meta.glob(["./**/*.tsx", "./**/*.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>
const isProductionSource = (path: string) => !/\.test\.|\.fixture\.|\/test\//.test(path)

function stripCssComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

/** Strips both `/* ... *\/` block comments and `// ...` line comments from
 *  TSX source: a class name mentioned
 *  only inside a `.tsx` COMMENT (e.g. a docstring citing a class, or a
 *  "TODO: reach for .foo here" note) must not count as a
 *  live consumer, so the component side is comment-stripped the
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
    .filter(([path]) => isProductionSource(path))
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
    const pattern = new RegExp(`(?<![A-Za-z0-9_-])${escapeRegExp(className)}(?![A-Za-z0-9_-])`)
    if (pattern.test(COMPONENT_TEXT)) return true
    return BUILT_CLASS_STEMS.has(builtStemOf(className) ?? "")
}

function escapeRegExp(text: string): string {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/** The built-class stem a class name starts with, if any. */
function builtStemOf(className: string): string | undefined {
    return [...BUILT_CLASS_STEMS.keys()].find((stem) => className.startsWith(stem))
}

/**
 * Classes the source builds from a stem and a value, as in `review-chip--${tone}`. Only these stems excuse a class
 * that never appears whole; any other template literal does not.
 */
const BUILT_CLASS_STEMS = new Map<string, string>([
    ["severity-", "curator task severity is appended to the stem when the class is chosen"],
    ["review-status-", "the review status is appended to the stem, with underscores turned into hyphens"],
    ["review-chip--", "the chip tone is appended to the stem"],
    ["mol-poster-atom--", "the element class is appended to the stem"],
    ["ktp-heat-", "the heat step is appended to the stem"],
    ["record-glyph--", "the record kind is appended to the stem"],
    ["settings-state--", "the setting's state is appended to the stem"],
])

// Explicit allowlist, with a REASON for each entry. A `Set` here (the
// plain list) would let an entry get added with no reason at all, silently.
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

describe("every class selector in a checked stylesheet has a real consumer", () => {
    it("checks every stylesheet but the token file", () => {
        expect(CHECKED_STYLESHEETS).toHaveLength(Object.keys(ALL_STYLESHEETS).length - UNCHECKED_STYLESHEETS.size)
        expect(CHECKED_STYLESHEETS).toContain("design-system.css")
    })

    it.each(CHECKED_STYLESHEETS)("%s: every class selector has at least one .tsx consumer (or a documented allowlist reason)", (stylesheetName) => {
        const css = ALL_STYLESHEETS[stylesheetName]
        const classNames = classNamesIn(css)
        expect(classNames.size).toBeGreaterThan(0)
        const deadClasses = Array.from(classNames)
            .filter((name) => !NOT_A_CLASS.has(name))
            .filter((name) => !(stylesheetName === "design-system.css" && isDesignSystemVocabulary(name)))
            .filter((name) => !hasComponentConsumer(name))
        expect(deadClasses).toEqual([])
    })

    it("a class no source uses is reported, even when its name starts like a used family", () => {
        expect(hasComponentConsumer("review-zombie-xyz")).toBe(false)
        expect(hasComponentConsumer("settings-zombie")).toBe(false)
        expect(hasComponentConsumer("review-status-approved")).toBe(true)
    })
})

describe("BUILT_CLASS_STEMS entries are reasoned and current", () => {
    it.each([...BUILT_CLASS_STEMS.keys()])("%s is still built in source as a template literal", (stem) => {
        expect(BUILT_CLASS_STEMS.get(stem)!.length).toBeGreaterThanOrEqual(20)
        expect(new RegExp(`(?<![A-Za-z0-9_-])${escapeRegExp(stem)}\\$\\{`).test(COMPONENT_TEXT), `${stem}\${...} appears nowhere in source -- stale entry`).toBe(true)
    })
})

describe("UNCHECKED_STYLESHEETS entries are real files", () => {
    it("names only stylesheets that exist", () => {
        for (const name of UNCHECKED_STYLESHEETS.keys()) expect(Object.keys(ALL_STYLESHEETS)).toContain(name)
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
