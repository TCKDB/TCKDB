import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { ConformerAttribution } from "../domain/conformerEvidence"
import { ConformerAttributionGroups } from "./ConformerAttributionGroups"

afterEach(cleanup)

type Rec = { id: string }

function renderGroups(attribution: ConformerAttribution<Rec>) {
    return render(
        <ConformerAttributionGroups
            attribution={attribution}
            selectedLabel="Conformer Group 2"
            renderRecord={(record) => <p key={record.id} data-testid={`record-${record.id}`}>{record.id}</p>}
            thisConformerNote="Traced to this conformer's own primary calculation."
            thisConformerEmptyText="No thermo record traces to this conformer yet."
            otherConformerNote="Traced to a different conformer than the one selected above."
            noLinkNote="No resolvable primary calculation."
            noLinkEmptyText="No entry-level thermo record is deposited for this entry."
        />,
    )
}

describe("ConformerAttributionGroups", () => {
    // The owner's exact report: "he opened the Thermochemistry tab having
    // selected conformer_2, and what sits there is another conformer's
    // record under a heading he has to read carefully to notice." A
    // selected conformer with ZERO records of its own must say so plainly,
    // and the OTHER conformer's records must not render as though they
    // belonged to the selected one.
    it("says plainly that the selected conformer has no records, and does not render another conformer's records as though they were its own", () => {
        renderGroups({
            thisConformer: [],
            otherConformers: [{ ref: "cg_one", label: "Conformer Group 1", records: [{ id: "a" }] }],
            noLink: [],
        })
        // The plain "no records" answer for the SELECTED conformer.
        const answer = screen.getByText("No thermo record traces to this conformer yet.")
        expect(answer).toBeVisible()
        expect(answer).toHaveClass("conformer-attribution-answer")
        // The other conformer's record is not inside the primary "From
        // Conformer Group 2" group -- it must never render as though it
        // belonged there.
        const primaryHeading = screen.getByRole("heading", { name: "From Conformer Group 2" })
        const primaryGroup = primaryHeading.closest(".conformer-evidence-group") as HTMLElement
        expect(within(primaryGroup).queryByTestId("record-a")).not.toBeInTheDocument()
        // It's still fully present, reachable -- inside the demoted,
        // collapsed "other conformers" disclosure, never deleted.
        const otherDetails = document.querySelector(".conformer-attribution-other") as HTMLDetailsElement
        expect(otherDetails).not.toBeNull()
        expect(otherDetails.open).toBe(false)
        expect(within(otherDetails).getByTestId("record-a")).toBeInTheDocument()
        expect(within(otherDetails).getByRole("heading", { name: "From Conformer Group 1" })).toBeInTheDocument()
    })

    it("does not demote or emphasize anything when the selected conformer DOES have its own records", () => {
        renderGroups({
            thisConformer: [{ id: "mine" }],
            otherConformers: [],
            noLink: [],
        })
        expect(screen.getByTestId("record-mine")).toBeVisible()
        expect(screen.queryByText("No thermo record traces to this conformer yet.")).not.toBeInTheDocument()
        expect(document.querySelector(".conformer-attribution-other")).toBeNull()
    })

    it("previews the record count in the other-conformers summary, and keeps every distinct other conformer separately labeled inside it", () => {
        renderGroups({
            thisConformer: [],
            otherConformers: [
                { ref: "cg_one", label: "Conformer Group 1", records: [{ id: "a" }, { id: "b" }] },
                { ref: "cg_three", label: "Conformer Group 3", records: [{ id: "c" }] },
            ],
            noLink: [],
        })
        const summary = screen.getByText("3 records from other conformers")
        expect(summary).toBeVisible()
        const details = summary.closest("details") as HTMLElement
        fireEvent.click(summary)
        expect(within(details).getByRole("heading", { name: "From Conformer Group 1" })).toBeVisible()
        expect(within(details).getByRole("heading", { name: "From Conformer Group 3" })).toBeVisible()
    })

    it("never renders a collapsed other-conformers disclosure when there are none", () => {
        renderGroups({ thisConformer: [{ id: "mine" }], otherConformers: [], noLink: [] })
        expect(screen.queryByText(/records? from other conformers/)).not.toBeInTheDocument()
        expect(document.querySelector(".conformer-attribution-other")).toBeNull()
    })

    it("keeps the no-conformer-link group at plain (non-emphasized) styling -- only the SELECTED conformer's empty state is emphasized", () => {
        renderGroups({ thisConformer: [{ id: "mine" }], otherConformers: [], noLink: [] })
        const noLinkText = screen.getByText("No entry-level thermo record is deposited for this entry.")
        expect(noLinkText).toHaveClass("empty-projection")
        expect(noLinkText).not.toHaveClass("conformer-attribution-answer")
    })
})
