import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ReviewChip } from "./ReviewChip"
import { GLOSSARY } from "../domain/glossary"

afterEach(cleanup)

function mark() {
    return document.querySelector(".review-chip-mark circle") as SVGCircleElement
}

describe("ReviewChip", () => {
    it.each([
        ["approved", "approved"],
        ["not_reviewed", "not reviewed"],
        ["under_review", "under review"],
        ["rejected", "rejected"],
        ["deprecated", "deprecated"],
    ])("shows %s as the word %s", (status, word) => {
        render(<ReviewChip status={status} />)
        expect(document.querySelector(".review-chip")).toHaveTextContent(word)
    })

    it("fills the mark for approved and outlines it for every other status", () => {
        const { rerender } = render(<ReviewChip status="approved" />)
        expect(mark().getAttribute("fill")).toBe("currentColor")
        for (const status of ["not_reviewed", "under_review", "rejected", "deprecated"]) {
            rerender(<ReviewChip status={status} />)
            expect(mark().getAttribute("fill")).toBe("none")
        }
    })

    it("hides the mark from assistive technology and still shows an unfamiliar status as a word", () => {
        render(<ReviewChip status="needs_second_look" />)
        expect(document.querySelector(".review-chip-mark")).toHaveAttribute("aria-hidden", "true")
        expect(document.querySelector(".review-chip")).toHaveTextContent("needs second look")
    })

    it("explains review through a glossary term that opens on Tab and closes on Esc", async () => {
        const user = userEvent.setup()
        render(<ReviewChip status="not_reviewed" />)
        await user.tab()
        const term = screen.getByRole("button", { name: "What review status means" })
        expect(term).toHaveFocus()
        const popover = screen.getByRole("tooltip", { hidden: true })
        expect(popover).toHaveTextContent(GLOSSARY["review-status"].definition)
        expect(popover).toHaveAttribute("data-open", "true")
        await user.keyboard("{Escape}")
        expect(popover).toHaveAttribute("data-open", "false")
        expect(term).toHaveFocus()
    })

    it("leaves out the term when explain is false", () => {
        render(<ReviewChip status="approved" explain={false} />)
        expect(screen.queryByRole("button")).toBeNull()
    })
})
