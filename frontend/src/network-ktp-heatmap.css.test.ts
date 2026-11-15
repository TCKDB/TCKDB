import { describe, expect, it } from "vitest"
import css from "./network-ktp-heatmap.css?raw"

function rule(selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    return new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? ""
}

describe("network-ktp-heatmap.css", () => {
    it("keeps the rate-record picker inside its column however long an option is", () => {
        expect(rule(".ktp-field")).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)/)
        expect(rule(".ktp-field select")).toMatch(/width:\s*100%/)
        expect(rule(".ktp-field select")).toMatch(/min-width:\s*0/)
    })
})
