import { describe, expect, it } from "vitest"
// `?raw` loads each stylesheet as plain source text -- see the comment atop
// `geometry-detail.css.test.ts` for why this suffix (not a bare import) is
// required under this project's `css: true` vitest config, and why
// `node:fs` is not an option for a file under `src/`.
import themeCss from "./theme.css?raw"
import indexCss from "./index.css?raw"
import speciesOverviewCss from "./species-overview.css?raw"
import speciesEntryCss from "./species-entry.css?raw"
import entryScienceCss from "./entry-science.css?raw"
import conformerGroupCss from "./conformer-group.css?raw"
import geometryDetailCss from "./geometry-detail.css?raw"
import calculationDetailCss from "./calculation-detail.css?raw"

/**
 * `theme.css` is the app's one and only place a colour is allowed to be a
 * literal. Every other stylesheet styles components through `var(--token)`
 * only -- that is what lets a single edit to `theme.css` retheme the whole
 * app, and what makes "light mode is pixel-identical before/after this
 * pass" a checkable claim rather than a hope: if a literal survived
 * somewhere, tokenising couldn't have moved it, so a hex string outside
 * this file is either a bug in this pass or a future regression the same
 * shape as the one this file guards against.
 *
 * New page stylesheets (a follow-up `*.css` file, e.g. a future component
 * page) should be added to `NON_TOKEN_STYLESHEETS` below and styled
 * through the same tokens -- that is the whole point of centralising them.
 */
const NON_TOKEN_STYLESHEETS: Record<string, string> = {
    "index.css": indexCss,
    "species-overview.css": speciesOverviewCss,
    "species-entry.css": speciesEntryCss,
    "entry-science.css": entryScienceCss,
    "conformer-group.css": conformerGroupCss,
    "geometry-detail.css": geometryDetailCss,
    "calculation-detail.css": calculationDetailCss,
}

const ALL_STYLESHEETS: Record<string, string> = {
    "theme.css": themeCss,
    ...NON_TOKEN_STYLESHEETS,
}

const HEX_LITERAL = /#[0-9a-fA-F]{3,8}\b/g
const RGBA_LITERAL = /rgba?\(\s*\d/g

/** Extracts the contents of the first brace-balanced block whose opening
 *  brace follows `startPattern`, e.g. `extractBlock(css, /^:root \{/m)`
 *  returns everything between that `:root {` and its matching `}` --
 *  balanced, so it does not stop at the first nested `}` inside a
 *  `@media` block. */
function extractBlock(source: string, startPattern: RegExp): string {
    const startMatch = startPattern.exec(source)
    if (!startMatch) throw new Error(`No match for ${startPattern} in stylesheet`)
    const braceStart = source.indexOf("{", startMatch.index)
    if (braceStart === -1) throw new Error(`No opening brace after match for ${startPattern}`)
    let depth = 0
    for (let i = braceStart; i < source.length; i++) {
        if (source[i] === "{") depth++
        else if (source[i] === "}") {
            depth--
            if (depth === 0) return source.slice(braceStart + 1, i)
        }
    }
    throw new Error(`Unbalanced braces after match for ${startPattern}`)
}

/** Strips `/* ... *\/` comments. Both helpers below run on
 *  comment-stripped text: this project's stylesheets narrate design
 *  history in comments (`geometry-detail.css`'s "this page's own literal
 *  tokens (--control-border/--muted/...)", `entry-science.css`'s retired
 *  "`var(--product)`" rail), and prose mentioning a token by name is not
 *  a live CSS dependency on it existing -- scanning comments here would
 *  demand every retired-token anecdote in the codebase stay a currently-
 *  defined token forever, which is backwards. */
function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

/** Custom-property NAMES *declared* in a block of CSS text, e.g. the `ink`
 *  in `    --ink: #16202c;`. A negative lookbehind excludes `--ink`
 *  immediately preceded by `(` -- a `var(--ink)` *usage*, not a
 *  declaration -- while still matching multiple declarations packed onto
 *  one line (`index.css`'s `:root { --serif: …; --sans: …; --mono: …; }`),
 *  which a line-start anchor alone would have missed for every property
 *  after the first. */
function declaredCustomProperties(block: string): Set<string> {
    const names = new Set<string>()
    for (const match of stripComments(block).matchAll(/(?<!\()--([a-zA-Z0-9-]+)\s*:/g)) {
        names.add(match[1])
    }
    return names
}

/** Custom-property names *referenced* via `var(--x)` (fallback ignored)
 *  anywhere in a block of CSS text. */
function referencedCustomProperties(source: string): Set<string> {
    const names = new Set<string>()
    for (const match of stripComments(source).matchAll(/var\(\s*--([a-zA-Z0-9-]+)/g)) {
        names.add(match[1])
    }
    return names
}

describe("no raw colour literals outside theme.css", () => {
    for (const [name, css] of Object.entries(NON_TOKEN_STYLESHEETS)) {
        it(`${name} contains no hex colour literal`, () => {
            expect(css.match(HEX_LITERAL)).toBeNull()
        })

        it(`${name} contains no rgb()/rgba() literal`, () => {
            expect(css.match(RGBA_LITERAL)).toBeNull()
        })
    }
})

describe("every var(--x) referenced is defined at :root somewhere", () => {
    // :root custom properties are global regardless of which file declares
    // them -- theme.css owns the colour tokens, index.css separately
    // declares the font tokens (--serif/--sans/--mono), which do not vary
    // by theme and so have no dark-mode block of their own. Both count.
    const rootDeclaredNames = new Set<string>([
        ...declaredCustomProperties(extractBlock(themeCss, /^:root \{/m)),
        ...declaredCustomProperties(extractBlock(indexCss, /^:root \{/m)),
    ])

    for (const [name, css] of Object.entries(ALL_STYLESHEETS)) {
        const referenced = referencedCustomProperties(css)
        for (const token of referenced) {
            it(`${name}: var(--${token}) is defined on :root`, () => {
                expect(rootDeclaredNames.has(token)).toBe(true)
            })
        }
    }
})

describe("the three theme blocks define the same set of token names", () => {
    // This is the highest-value check in this file: a token present in
    // light but silently missing from a dark block does not fail to
    // build and does not fail any snapshot -- it renders as the
    // *previous* cascade value (inherited from a lower-specificity rule,
    // or the browser default), which for a colour token is exactly the
    // "unreadable text on the wrong background" bug dark mode exists to
    // avoid. Comparing SETS of names (not counts, not order) is what
    // catches a rename or deletion in any one block without the other
    // two changing.
    const lightNames = declaredCustomProperties(extractBlock(themeCss, /^:root \{/m))

    // Anchored to the start of a line: theme.css's own header comment also
    // mentions `@media (prefers-color-scheme: dark)` in prose (indented
    // under a `*`), which is not where the live rule starts.
    const mediaBlock = extractBlock(themeCss, /^@media \(prefers-color-scheme: dark\) \{/m)
    const mediaDarkNames = declaredCustomProperties(
        extractBlock(mediaBlock, /:root:not\(\[data-theme="light"]\) \{/)
    )

    const explicitDarkNames = declaredCustomProperties(
        extractBlock(themeCss, /^:root\[data-theme="dark"] \{/m)
    )

    it("all three blocks declare at least one token (sanity check on the extraction itself)", () => {
        expect(lightNames.size).toBeGreaterThan(0)
        expect(mediaDarkNames.size).toBeGreaterThan(0)
        expect(explicitDarkNames.size).toBeGreaterThan(0)
    })

    it("prefers-color-scheme dark block matches the light block's token names", () => {
        expect(new Set(mediaDarkNames)).toEqual(new Set(lightNames))
    })

    it("[data-theme=dark] block matches the light block's token names", () => {
        expect(new Set(explicitDarkNames)).toEqual(new Set(lightNames))
    })

    it("prefers-color-scheme dark block matches [data-theme=dark] block's token names", () => {
        expect(new Set(mediaDarkNames)).toEqual(new Set(explicitDarkNames))
    })
})
