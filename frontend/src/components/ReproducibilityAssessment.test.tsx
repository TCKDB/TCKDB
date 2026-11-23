import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { ReproducibilityAssessment } from "./ReproducibilityAssessment"
import vocabulary from "../domain/apiVocabulary.json"

afterEach(cleanup)

describe("ReproducibilityAssessment", () => {
    it.each(Object.entries(vocabulary.reproducibilityGrade))("shows %s with its exact one-line meaning", (grade, meaning) => {
        render(<ReproducibilityAssessment idBase="thm_1" reproducibility={{ state: "current", grade, assessed_at: "2026-10-02T09:00:00", made_under: { state: "inputs_unchecked", rubric: "reproducibility", assessed_version: 1, current_version: 1 } }} />)
        expect(screen.getByText(grade.replaceAll("_", " "))).toHaveClass("value-pill")
        expect(screen.getByText(meaning)).toBeInTheDocument()
        expect(screen.getByText("Assessed 2026-10-02")).toBeInTheDocument()
        expect(document.querySelector('[data-component="made-under"]')).toHaveAttribute("data-state", "inputs_unchecked")
    })

    it("shows no grade for a record nobody has assessed", () => {
        render(<ReproducibilityAssessment idBase="thm_1" reproducibility={{ state: "unassessed", grade: null }} />)
        expect(screen.getByText("not assessed")).toBeInTheDocument()
        expect(document.querySelector(".value-pill:not(.value-pill--muted)")).toBeNull()
        expect(document.querySelector('[data-component="made-under"]')).toHaveAttribute("data-state", "unassessed")
    })

    it("marks a stale assessment", () => {
        render(<ReproducibilityAssessment idBase="thm_1" reproducibility={{ state: "stale", grade: "described", made_under: { state: "stale", rubric: "reproducibility", assessed_version: 1, current_version: 2, stale_reason: "rubric_version" } }} />)
        expect(document.querySelector('[data-component="made-under"]')).toHaveAttribute("data-state", "stale")
    })

    it("words an unknown grade without a meaning and renders nothing for no assessment", () => {
        const { container, rerender } = render(<ReproducibilityAssessment idBase="x" reproducibility={{ state: "current", grade: "new_grade" }} />)
        expect(screen.getByText("new grade")).toBeInTheDocument()
        rerender(<ReproducibilityAssessment idBase="x" reproducibility={null} />)
        expect(container).toBeEmptyDOMElement()
    })
})
