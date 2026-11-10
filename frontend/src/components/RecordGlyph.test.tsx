import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { RecordGlyph, type RecordKind } from "./RecordGlyph"

afterEach(cleanup)

const KINDS: RecordKind[] = ["species", "species-entry", "conformer-group", "calculation", "reaction", "transition-state", "network"]

describe("RecordGlyph", () => {
    it("is hidden from assistive technology by default", () => {
        const { container } = render(<RecordGlyph kind="species" />)
        const svg = container.querySelector("svg")!
        expect(svg).toHaveAttribute("aria-hidden", "true")
        expect(svg).not.toHaveAttribute("role")
        expect(svg.querySelector("title")).toBeNull()
    })

    it("becomes a labelled image when given a label", () => {
        render(<RecordGlyph kind="reaction" label="Reaction" />)
        const image = screen.getByRole("img", { name: "Reaction" })
        expect(image.querySelector("title")).toHaveTextContent("Reaction")
        expect(image).not.toHaveAttribute("aria-hidden")
    })

    it("draws a distinct shape for each of the seven kinds", () => {
        const shapes = KINDS.map((kind) => {
            const { container, unmount } = render(<RecordGlyph kind={kind} />)
            const markup = container.querySelector("svg")!.innerHTML
            unmount()
            return markup
        })
        expect(new Set(shapes).size).toBe(KINDS.length)
    })

    it("colours each kind from a --kind-* token", () => {
        const { container } = render(<><RecordGlyph kind="transition-state" /><RecordGlyph kind="network" /></>)
        const classes = [...container.querySelectorAll("svg")].map((svg) => svg.getAttribute("class"))
        expect(classes[0]).toContain("record-glyph--ts")
        expect(classes[1]).toContain("record-glyph--network")
    })
})
