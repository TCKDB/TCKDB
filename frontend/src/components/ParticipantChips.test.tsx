import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
import { ParticipantChips } from "./ParticipantChips"

afterEach(cleanup)

describe("ParticipantChips", () => {
    it("puts each chip in its own no-wrap span, the leading dot with the first chip and the commas with the chip before", () => {
        const { container } = render(<span><ParticipantChips chips={["excited state", "state X \u00b3\u03a3g\u207b"]} /></span>)
        const spans = Array.from(container.querySelectorAll(".chip-nowrap")).map((node) => node.textContent)
        expect(spans).toEqual(["\u00b7 excited state,", "state X \u00b3\u03a3g\u207b"])
        expect(container.textContent).toBe(" \u00b7 excited state, state X \u00b3\u03a3g\u207b")
    })

    it("opens and closes a parenthesised run inside the first and last chip", () => {
        const { container } = render(<span><ParticipantChips chips={["a b", "c d"]} style="paren" /></span>)
        expect(Array.from(container.querySelectorAll(".chip-nowrap")).map((node) => node.textContent)).toEqual(["(a b,", "c d)"])
    })

    it("renders nothing for no chips", () => {
        const { container } = render(<span><ParticipantChips chips={[]} /></span>)
        expect(container.textContent).toBe("")
    })
})
