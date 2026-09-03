import { describe, expect, it } from "vitest"
// `?raw` loads each stylesheet as plain source text -- see the comment atop
// `geometry-detail.css.test.ts` for why this suffix (not a bare import) is
// required under this project's `css: true` vitest config, and why
// `node:fs` is not an option for a file under `src/`.
import themeCss from "./theme.css?raw"

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
 * New page stylesheets are picked up automatically (see the glob below);
 * they must style through the same tokens -- that is the whole point of
 * centralising them.
 */
// DISCOVERED, never enumerated. An explicit list is a guard pointed at a
// fixed set of targets, and this one was already escaped once: #295 landed
// `geometry-measure.css` with 22 hex literals while this suite was green,
// because a file absent from the list is not "passing" -- it is unexamined,
// and the two are indistinguishable from the outside. The comment that used
// to sit here asked a future author to remember to add their file. Asking is
// not a guard. `import.meta.glob` enumerates what actually exists, so a new
// stylesheet is covered the moment it is created and a deletion cannot
// silently shrink the checked set.
const STYLESHEET_SOURCES = import.meta.glob("./*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>

const NON_TOKEN_STYLESHEETS: Record<string, string> = Object.fromEntries(
    Object.entries(STYLESHEET_SOURCES)
        .map(([path, css]) => [path.replace(/^\.\//, ""), css])
        .filter(([name]) => name !== "theme.css"),
)

const ALL_STYLESHEETS: Record<string, string> = {
    "theme.css": themeCss,
    ...NON_TOKEN_STYLESHEETS,
}

// The discovery itself needs an assertion: a glob that silently matched
// nothing would make every loop below vacuous -- zero files scanned, zero
// failures, green. Pin a floor and name a file that must always be there.
describe("the stylesheet inventory is discovered, not assumed", () => {
    it("finds every stylesheet in src/, not a hand-maintained subset", () => {
        const names = Object.keys(NON_TOKEN_STYLESHEETS)
        expect(names.length).toBeGreaterThanOrEqual(8)
        expect(names).toContain("geometry-measure.css")  // the file the old allowlist missed
        expect(names).toContain("index.css")
    })
})

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
        ...declaredCustomProperties(extractBlock(NON_TOKEN_STYLESHEETS["index.css"], /^:root \{/m)),
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

/**
 * Accessibility pass (review finding 10): `--faint` and `--muted-2` back
 * the smallest, most-repeated text in the app -- browse `spc_` refs,
 * `.ref-item-label` captions, the COPY button label, the conformer-card
 * coverage line, filter hints -- and measured well under WCAG AA's 4.5:1
 * on every background they actually render on, as low as 2.17:1 (light
 * `--faint` on `--accent-50`, the tint a SELECTED `.conformer-card` gets;
 * the COPY button and coverage line sit directly on that tint, not just
 * on plain white).
 *
 * A value that only clears 4.5:1 on plain white is not actually fixed --
 * this checks all four real backgrounds these tokens render on
 * (`--surface`, `--page-bg`, `--surface-sunken`, `--accent-50`), per
 * theme, computed from the LIVE token values in this file (not a
 * hardcoded expectation disconnected from it), so a future edit that
 * quietly darkens `--accent-50` or lightens `--faint`/`--muted-2` back
 * toward the original failing values fails here instead of shipping.
 */
describe("--faint and --muted-2 clear 4.5:1 on every background they render on", () => {
    // Handles both `#rrggbb` and the shorthand `#rgb` (`--surface: #fff` in
    // the light block) -- a real form this file uses, not a hypothetical.
    function hexOf(block: string, name: string): [number, number, number] {
        const match = new RegExp(`(?<!\\()--${name}:\\s*#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\\b`).exec(block)
        if (!match) throw new Error(`--${name} not found as a #rgb/#rrggbb declaration`)
        const hex = match[1].length === 3 ? match[1].split("").map((c) => c + c).join("") : match[1]
        return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)]
    }

    function relativeLuminance([r, g, b]: [number, number, number]): number {
        const channel = (c: number) => {
            const s = c / 255
            return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
        }
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    }

    // WCAG 2 contrast ratio: (L_lighter + 0.05) / (L_darker + 0.05).
    function contrastRatio(a: [number, number, number], b: [number, number, number]): number {
        const [lighter, darker] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x)
        return (lighter + 0.05) / (darker + 0.05)
    }

    const lightBlock = extractBlock(themeCss, /^:root \{/m)
    const darkBlock = extractBlock(themeCss, /^:root\[data-theme="dark"] \{/m)

    const BACKGROUND_NAMES = ["surface", "page-bg", "surface-sunken", "accent-50"]

    for (const theme of ["light", "dark"] as const) {
        const block = theme === "light" ? lightBlock : darkBlock
        const backgrounds = Object.fromEntries(BACKGROUND_NAMES.map((n) => [n, hexOf(block, n)]))

        for (const tokenName of ["faint", "muted-2"]) {
            it(`${theme} --${tokenName} clears 4.5:1 against --surface/--page-bg/--surface-sunken/--accent-50`, () => {
                const fg = hexOf(block, tokenName)
                for (const [bgName, bg] of Object.entries(backgrounds)) {
                    const ratio = contrastRatio(fg, bg)
                    expect(ratio, `--${tokenName} vs --${bgName} in ${theme} mode (got ${ratio.toFixed(2)}:1)`)
                        .toBeGreaterThanOrEqual(4.5)
                }
            })
        }
    }
})
