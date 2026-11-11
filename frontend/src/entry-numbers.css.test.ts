import { describe, expect, it } from "vitest"
import css from "./entry-numbers.css?raw"

/** The declarations of the first rule whose selector list names exactly `selector`. */
function rule(source: string, selector: string): string {
    for (const match of source.matchAll(/(?:^|\n)([^{}@]+)\{([^}]*)\}/g)) {
        if (match[1].split(",").some((candidate) => candidate.trim() === selector)) return match[2]
    }
    return ""
}

describe("entry-numbers.css", () => {
    it("styles links in the section intro like every other in-page link, with a focus ring", () => {
        expect(rule(css, ".entry-numbers .section-intro a")).toContain("text-decoration: underline")
        expect(rule(css, ".entry-numbers .section-intro a:focus-visible")).toContain("outline: 3px solid var(--focus-ring)")
    })

    it("stacks the record above its values on a phone, each label above its value", () => {
        const phone = css.slice(css.indexOf("@media (max-width: 40rem)"))
        expect(phone).toContain("display: block")
        expect(phone).toMatch(/\.entry-numbers-values \{[^}]*grid-template-columns: 1fr/)
        expect(phone).toMatch(/\.entry-numbers-table \{[^}]*min-width: 0/)
        // Long wording (a statmech treatment) must wrap on a phone instead of forcing a sideways scroll.
        expect(phone).toMatch(/\.entry-numbers-values dd \{[^}]*white-space: normal/)
    })

    it("lets other-version cards shrink and wrap a long ref at 320px", () => {
        expect(rule(css, ".entry-versions-item")).toContain("min-width: 0")
        expect(rule(css, ".entry-versions-item")).toMatch(/grid-template-columns: minmax\(0, 1fr\)/)
        expect(rule(css, ".entry-versions-meta code")).toContain("overflow-wrap: anywhere")
        expect(rule(css, ".entry-versions-title")).toContain("overflow-wrap: anywhere")
    })
})

const numbersCss = css

/** The declaration block of one non-nested selector; throws when it is missing so a rename fails loudly. */
function extractRule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(source)
    if (!match) throw new Error(`No rule found for selector "${selector}"`)
    return match[1]
}

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
