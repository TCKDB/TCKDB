import { describe, expect, it } from "vitest"
// Vite's `?raw` import suffix (typed by the `vite/client` types this app's
// tsconfig already includes) loads the file as a plain string, unprocessed —
// deliberately NOT a bare `./geometry-detail.css` import, which under this
// project's `css: true` vitest config would inject the stylesheet instead of
// handing back its source text. `node:fs` would also work at test-run time,
// but `src/**` is compiled by `tsconfig.app.json`, which has no `"node"`
// entry in `types` (only `tsconfig.node.json`, scoped to `vite.config.ts`,
// does) — `node:fs` fails `tsc -b` for every file under `src/`, this one
// included.
import css from "./geometry-detail.css?raw"

/**
 * The page's own rules. The viewer's rules, and their tests, are in `geometry-viewer.css` / `geometry-viewer.css.test.ts`.
 */

/** Extracts the declaration block for a single, non-nested selector (not inside
 *  an `@media` block) as raw text, e.g. `extractRule(css, ".viewer-stage")`
 *  returns everything between `.viewer-stage {` and its matching `}`. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector ${selector} in geometry-detail.css`)
    return match[1]
}

describe("coordinate toggle buttons use --type-ui, not a one-off uppercase mono", () => {
    it(".coordinate-toggle button uses --type-ui-font, no uppercase transform, no .72rem literal", () => {
        const rule = extractRule(css, ".coordinate-toggle button")
        expect(rule).toMatch(/font:\s*var\(--type-ui-font\)/)
        expect(rule).not.toMatch(/text-transform:\s*uppercase/)
        expect(rule).not.toMatch(/\.72rem/)
    })
})

describe("the viewer's own rules are not declared here", () => {
    it("declares no .viewer-* or .geometry-viewer rule", () => {
        const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "")
        expect(withoutComments).not.toMatch(/\.viewer-|\.geometry-viewer/)
    })
})

describe(".validation-card and the metric tile row are retired from this file", () => {
    it("declares no .validation-card or .geometry-summary rule", () => {
        const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "")
        expect(withoutComments).not.toMatch(/\.validation-card|\.geometry-summary/)
    })
})
