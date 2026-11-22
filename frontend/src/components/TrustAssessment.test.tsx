import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { TrustAssessment } from "./TrustAssessment"
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
        expect(screen.getByText(/Evidence completeness of 0\.50 or better/)).toBeInTheDocument()
        expect(screen.getByText("2 of 4 weighted checks passed")).toBeInTheDocument()
        expect(screen.getByText("under review")).toBeInTheDocument()
        expect(screen.getByText(/A curator has this record open\./)).toBeInTheDocument()
        expect(document.querySelector('[data-component="made-under"]')).not.toBeNull()
    })

    it("lists every check in served order with its outcome and what the rubric asks", () => {
        render(<TrustAssessment trust={TRUST} headingId="trust-x" />)
        const rows = Array.from(document.querySelectorAll("tbody tr")).map((tr) => Array.from(tr.querySelectorAll("td")).map((td) => td.textContent))
        expect(rows.map((row) => row[0])).toEqual([
            "arrhenius parameters complete", "arrhenius units present", "geometry validation not failed for source calculations",
            "irc evidence present", "brand new check",
        ])
        expect(rows[0]).toEqual(["arrhenius parameters complete", "passed", "Arrhenius-family kinetics should include A, A units, n, and Ea."])
        expect(rows[1][1]).toBe("missing")
        expect(rows[2][1]).toBe("warning, needs investigation")
        expect(rows[3][1]).toBe("not applicable")
        // Unknown tokens: words only, no invented meaning.
        expect(rows[4]).toEqual(["brand new check", "brand new outcome", ""])
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
        expect(note.textContent).toContain("a curator marked it unusable")
        const badge = screen.getByText("hard failed")
        expect(note.compareDocumentPosition(badge) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        expect(document.querySelector("details")).toHaveAttribute("open")
    })

    it("words an unknown hard-fail reason and status without a meaning or an error", () => {
        render(<TrustAssessment trust={{ ...TRUST, trust_status: "new_status", evidence: { ...TRUST.evidence, hard_fail_reason: "some_new_reason" } }} headingId="trust-x" />)
        expect(screen.getByText("Hard failure: some new reason")).toBeInTheDocument()
        expect(screen.getByText("new status")).toBeInTheDocument()
    })

    it("never shows the internal record id or certification", () => {
        render(<TrustAssessment trust={TRUST} headingId="trust-x" />)
        expect(document.body.innerHTML).not.toContain("987654")
        expect(document.body.textContent).not.toMatch(/certified/i)
    })
})
