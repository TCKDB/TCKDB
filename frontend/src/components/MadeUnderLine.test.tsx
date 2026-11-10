import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import "../design-system.css"
import { MadeUnderLine } from "./MadeUnderLine"

afterEach(cleanup)

const base = { rubric: "tckdb_reproducibility", current_rubric: "tckdb_reproducibility" }

describe("MadeUnderLine", () => {
    it("current: one line naming rubric and version, with no badge", () => {
        const { container } = render(<MadeUnderLine madeUnder={{ ...base, assessed_version: 1, current_version: 1, state: "current" }} />)
        const line = container.querySelector('[data-component="made-under"]')
        expect(line).toHaveAttribute("data-state", "current")
        expect(screen.getByText(/version 1, the current version/)).toBeVisible()
        expect(container.querySelector(".value-pill")).toBeNull()
    })

    it("stale: a Stale badge beside the plain explanation", () => {
        const { container } = render(
            <MadeUnderLine madeUnder={{ ...base, assessed_version: 1, current_version: 2, state: "stale", stale_reason: "rubric_version" }} />,
        )
        expect(container.querySelector('[data-component="made-under"]')).toHaveAttribute("data-state", "stale")
        const badge = screen.getByText("Stale")
        expect(badge).toHaveClass("value-pill", "made-under-badge-stale")
        expect(screen.getByText(/The current version is 2\. It has not been re-assessed under the current version\./)).toBeVisible()
    })

    it("version 0: stale, with the pre-reset wording and no raw v0", () => {
        const { container } = render(
            <MadeUnderLine madeUnder={{ ...base, assessed_version: 0, current_version: 1, state: "stale", stale_reason: "rubric_version" }} />,
        )
        expect(screen.getByText("Stale")).toBeVisible()
        expect(screen.getByText(/stamped before the 2026-10 versioning reset/)).toBeVisible()
        expect(container.textContent).not.toMatch(/\bv0\b/)
    })

    it("unassessed: a muted Not assessed badge, and it is not the stale badge", () => {
        const { container } = render(<MadeUnderLine madeUnder={{ state: "unassessed" }} />)
        expect(container.querySelector('[data-component="made-under"]')).toHaveAttribute("data-state", "unassessed")
        const badge = screen.getByText("Not assessed")
        expect(badge).toHaveClass("value-pill--muted")
        expect(badge).not.toHaveClass("made-under-badge-stale")
        expect(screen.queryByText("Stale")).toBeNull()
    })

    it("inputs_unchecked: no badge, and the line says the inputs were not checked", () => {
        const { container } = render(
            <MadeUnderLine madeUnder={{ ...base, assessed_version: 1, current_version: 1, state: "inputs_unchecked", inputs_checked: false }} />,
        )
        expect(container.querySelector('[data-component="made-under"]')).toHaveAttribute("data-state", "inputs_unchecked")
        expect(screen.getByText(/Whether its inputs have changed since was not checked on this page\./)).toBeVisible()
        expect(container.querySelector(".value-pill")).toBeNull()
    })

    it("kind review: worded as a review", () => {
        render(
            <MadeUnderLine
                kind="review"
                madeUnder={{ ...base, assessed_version: 0, current_version: 1, state: "stale", stale_reason: "rubric_version" }}
            />,
        )
        expect(screen.getByText(/^Reviewed under rubric tckdb reproducibility/)).toBeVisible()
    })

    it("renders nothing when the API served no made_under", () => {
        const { container } = render(<MadeUnderLine madeUnder={null} />)
        expect(container).toBeEmptyDOMElement()
    })
})
