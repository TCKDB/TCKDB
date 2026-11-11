import { describe, expect, it } from "vitest"
import stage from "./viewer-stage.css?raw"
import molecule from "./molecule-page.css?raw"

function rule(source: string, selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const match = new RegExp(`^${escaped}\\s*\\{([^}]*)\\}`, "m").exec(source)
    if (!match) throw new Error(`No rule for ${selector}`)
    return match[1]
}

describe("links beside a picture and in a Used by list look like links", () => {
    for (const [name, source, selector] of [
        ["figure caption", stage, ".viewer-figure figcaption a"],
        ["Used by list", molecule, ".shape-usedby a"],
    ] as const) {
        it(`${name}: accent colour, underline, and a focus ring`, () => {
            const body = rule(source, selector)
            expect(body).toMatch(/color:\s*var\(--accent\)/)
            expect(body).toMatch(/text-decoration:\s*underline/)
            expect(body).toMatch(/text-underline-offset:\s*\.16em/)
            expect(rule(source, `${selector}:focus-visible`)).toMatch(/outline:\s*3px solid var\(--focus-ring\)/)
        })
    }
})

describe("Show in 3D is tap-sized", () => {
    it("declares min-height 2.75rem", () => {
        expect(rule(stage, ".viewer-poster-action")).toMatch(/min-height:\s*2\.75rem/)
    })
})
