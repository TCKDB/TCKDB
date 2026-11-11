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

describe("species-overview.css: no page container of its own", () => {
    it("leaves the page width and gutter to the page shell", () => {
        expect(css).not.toMatch(/\.species-overview\s*\{/)
        expect(css).not.toMatch(/max-width:\s*126rem/)
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

    it("the version links, the order-note link and the level toggle are 44px high on a phone", () => {
        const phone = numbersCss.slice(numbersCss.indexOf("@media (max-width: 40rem)"))
        const links = /\.entry-versions-title a,\s*\.entry-numbers-version a,\s*\.entry-numbers \.order-note a\s*\{([^}]*)\}/.exec(phone)
        expect(links, "the phone rule for links").not.toBeNull()
        expect(links![1]).toMatch(/display:\s*inline-flex/)
        expect(links![1]).toMatch(/align-items:\s*center/)
        expect(links![1]).toMatch(/min-height:\s*44px/)
        expect(/\.entry-numbers-toggle\s*\{([^}]*)\}/.exec(phone)?.[1]).toMatch(/min-height:\s*44px/)
    })

    it("the version line wraps long refs instead of widening the row", () => {
        expect(extractRule(numbersCss, ".entry-numbers-version")).toMatch(/overflow-wrap:\s*anywhere/)
    })
})
