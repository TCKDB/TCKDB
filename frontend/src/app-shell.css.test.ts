import { describe, expect, it } from "vitest"
import shellCss from "./app-shell.css?raw"
import browseCss from "./browse.css?raw"

// Every link and control the shell adds must draw the 3px focus ring; a new one that does not fails here.
const RING = /outline:\s*3px solid var\(--focus-ring\)/

function focusRuleDeclarations(source: string, selector: string): string | null {
    for (const match of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const selectors = match[1].replace(/\/\*[\s\S]*?\*\//g, "").split(",").map((part) => part.trim())
        if (selectors.includes(selector)) return match[2]
    }
    return null
}

describe("shell links and controls keep the focus ring", () => {
    const cases: [string, string][] = [
        [".brand:focus-visible", shellCss],
        [".site-nav a:focus-visible", shellCss],
        [".tab-bar-item:focus-visible", shellCss],
        [".more-menu-panel a:focus-visible", shellCss],
        [".auth-status-link:focus-visible", shellCss],
        [".browse-related a:focus-visible", browseCss],
    ]
    it.each(cases)("%s", (selector, source) => {
        expect(focusRuleDeclarations(source, selector)).toMatch(RING)
    })

    it("every link-like selector in app-shell.css has a focus-visible rule", () => {
        const linkSelectors = new Set<string>()
        for (const match of shellCss.matchAll(/([^{}]+)\{/g)) {
            for (const raw of match[1].replace(/\/\*[\s\S]*?\*\//g, "").split(",")) {
                const selector = raw.trim().replace(/\[[^\]]*\]/g, "").replace(/:(hover|focus-visible)$/, "")
                if (/(^|\s)a$|^\.tab-bar-item$|^\.brand$/.test(selector) && !selector.includes("@")) linkSelectors.add(selector)
            }
        }
        expect(linkSelectors.size).toBeGreaterThan(0)
        for (const selector of linkSelectors) {
            const withoutChild = selector.replace(/ > /g, " ")
            expect(focusRuleDeclarations(shellCss, `${withoutChild}:focus-visible`), selector).toMatch(RING)
        }
    })
})
