import { describe, expect, it } from "vitest"
// `?raw` loads the file as plain source text, not a processed stylesheet.
import css from "./species-overview.css?raw"
import numbersCss from "./entry-numbers.css?raw"

/** The declaration block of one non-nested selector; throws when it is missing so a rename fails loudly. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector "${selector}"`)
    return match[1]
}

describe("species-overview.css: the page container only", () => {
    it("caps the page at the record-page width with the shared gutter", () => {
        const rule = extractRule(css, ".species-overview")
        expect(rule).toMatch(/max-width:\s*126rem/)
        expect(rule).toMatch(/padding:/)
    })

    it("no longer carries the header, entry-card or state-group rules the shared record blocks replaced", () => {
        for (const retired of [".species-header", ".species-identity-grid", ".entry-index", ".entry-state-group", ".entry-card", ".entry-rows", ".entry-facts"]) {
            expect(css, retired).not.toContain(retired)
        }
    })

    it("uses no off-token divider colour", () => {
        expect(css).not.toMatch(/var\(--divider-strong\)/)
    })
})

describe("entry-numbers.css: the version line and the version list on a phone", () => {
    it("the version line names its links with the house underline and a focus ring", () => {
        expect(numbersCss).toMatch(/\.entry-numbers-version a:focus-visible/)
        expect(numbersCss).toMatch(/\.entry-numbers-version a,/)
    })

    it("the version links get a 44px-high target on a phone", () => {
        const phone = numbersCss.slice(numbersCss.indexOf("@media (max-width: 40rem)"))
        expect(phone).toMatch(/\.entry-versions-title a,\s*\.entry-numbers-version a\s*\{[^}]*padding-block:\s*\.7rem/)
    })

    it("the version line wraps long refs instead of widening the row", () => {
        expect(extractRule(numbersCss, ".entry-numbers-version")).toMatch(/overflow-wrap:\s*anywhere/)
    })
})
