import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { HardFailFlag, TrustAssessment } from "./TrustAssessment"
import type { TrustFragment } from "../api/trustSchema"

afterEach(cleanup)

const TRUST: TrustFragment = {
    review_status: "under_review",
    trust_status: "partial",
    evidence: {
        passed_count: 2,
        possible_count: 4,
        rubric: "computed_kinetics",
        rubric_version: 1,
        checks: {
            arrhenius_parameters_complete: "passed",
            arrhenius_units_present: "missing",
            geometry_validation_not_failed_for_source_calculations: "warning",
            irc_evidence_present: "not_applicable",
            brand_new_check: "brand_new_outcome",
        },
        hard_fail_reason: null,
        record_id: 987654,
    },
    is_certified: false,
}

describe("TrustAssessment", () => {
    it("words the badge, the weighted count, the review status and the made-under line", () => {
        render(<TrustAssessment trust={TRUST} headingId="trust-x" />)
        expect(screen.getByText("partial")).toHaveClass("value-pill")
        expect(screen.getByText(/At least 50% of the weighted checks passed\./)).toBeInTheDocument()
        expect(screen.getByText("2 of 4 weighted checks passed")).toBeInTheDocument()
        expect(screen.getByText("under review")).toBeInTheDocument()
        expect(screen.getByText("A curator is working on this record now.")).toBeInTheDocument()
        expect(document.querySelector('[data-component="made-under"]')).not.toBeNull()
    })

    it("lists every check in served order with its outcome and what the rubric asks", () => {
        render(<TrustAssessment trust={TRUST} headingId="trust-x" />)
        const rows = Array.from(document.querySelectorAll("tbody tr")).map((tr) => Array.from(tr.querySelectorAll("th, td")).map((td) => td.textContent))
        expect(rows.map((row) => row[0])).toEqual([
            "Arrhenius parameters complete", "Arrhenius units present", "Geometry validation not failed for source calculations",
            "IRC evidence present", "Brand new check",
        ])
        expect(rows[0]).toEqual(["Arrhenius parameters complete", "passed", "An Arrhenius-form rate should give A, its units, n and the activation energy."])
        expect(rows[1][1]).toBe("missing")
        expect(rows[2][1]).toBe("warning, needs investigation")
        expect(rows[3][1]).toBe("not applicable")
        // Unknown tokens: words only, no invented meaning.
        expect(rows[4]).toEqual(["Brand new check", "brand new outcome", ""])
        expect(screen.getByText("Every check")).toBeInTheDocument()
    })

    it("opens the checks when one is missing and leaves a complete verdict collapsed", () => {
        const { unmount } = render(<TrustAssessment trust={TRUST} headingId="trust-x" />)
        expect(document.querySelector("details")).toHaveAttribute("open")
        unmount()
        render(<TrustAssessment trust={{ ...TRUST, trust_status: "well_supported", evidence: { ...TRUST.evidence, checks: { arrhenius_parameters_complete: "passed" } } }} headingId="trust-y" />)
        expect(document.querySelector("details")).not.toHaveAttribute("open")
    })

    it("puts a hard failure before the badge and opens the checks", () => {
        render(<TrustAssessment trust={{ ...TRUST, trust_status: "hard_failed", evidence: { ...TRUST.evidence, hard_fail_reason: "calculation_rejected" } }} headingId="trust-x" />)
        const note = document.querySelector('[data-component="trust-hard-fail"]')!
        expect(note.textContent).toContain("Hard failure: calculation rejected")
        expect(note.textContent).toContain("A curator marked the calculation unusable.")
        const badge = screen.getByText("hard failed")
        expect(note.compareDocumentPosition(badge) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })

    it("opens the checks for a hard failure even when every check passed", () => {
        render(<TrustAssessment trust={{ ...TRUST, trust_status: "hard_failed", evidence: { ...TRUST.evidence, checks: { arrhenius_parameters_complete: "passed" }, hard_fail_reason: "calculation_rejected" } }} headingId="trust-x" />)
        expect(document.querySelector("details")).toHaveAttribute("open")
    })

    it("words an unknown hard-fail reason and status without a meaning or an error", () => {
        render(<TrustAssessment trust={{ ...TRUST, trust_status: "new_status", evidence: { ...TRUST.evidence, hard_fail_reason: "some_new_reason" } }} headingId="trust-x" />)
        expect(screen.getByText("Hard failure: some new reason")).toBeInTheDocument()
        expect(screen.getByText("new status")).toBeInTheDocument()
    })

    it("makes the check table a named, focusable scroll region with the check name as its row header", () => {
        render(<TrustAssessment trust={TRUST} headingId="trust-x" recordLabel="statmech record 2" />)
        const region = screen.getByRole("region", { name: "Every trust check of statmech record 2" })
        expect(region).toHaveAttribute("tabindex", "0")
        expect(screen.getByRole("rowheader", { name: "Arrhenius units present" })).toBeInTheDocument()
        expect(screen.getByRole("region", { name: "Trust of statmech record 2" })).toBeInTheDocument()
    })

    it("marks a missing check and a hard failure in the attention tone, and can leave out the review status", () => {
        render(<TrustAssessment trust={{ ...TRUST, trust_status: "hard_failed" }} headingId="trust-x" showReviewStatus={false} />)
        expect(screen.getByText("missing")).toHaveClass("trust-pill-attention")
        expect(screen.getByText("hard failed")).toHaveClass("trust-pill-attention")
        expect(screen.getByText("passed")).not.toHaveClass("trust-pill-attention")
        expect(screen.queryByText("Review status")).toBeNull()
    })

    it("never shows the internal record id or certification", () => {
        render(<TrustAssessment trust={TRUST} headingId="trust-x" />)
        expect(document.body.innerHTML).not.toContain("987654")
        expect(document.body.textContent).not.toMatch(/certified/i)
    })
})

describe("HardFailFlag", () => {
    it("flags only a hard-failed record", () => {
        const { container, rerender } = render(<HardFailFlag trust={{ trust_status: "hard_failed" }} />)
        expect(container).toHaveTextContent("hard failed")
        rerender(<HardFailFlag trust={{ trust_status: "partial" }} />)
        expect(container).toBeEmptyDOMElement()
    })
})
