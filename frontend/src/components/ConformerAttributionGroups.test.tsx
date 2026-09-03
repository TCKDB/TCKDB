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
            renderRecords={(records) => records.map((record) => <p key={record.id} data-testid={`record-${record.id}`}>{record.id}</p>)}
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

    // Finding 4 of the BLOCK review: `partitionByConformerLink` files a
    // record naming MULTIPLE other groups under EVERY one of those groups'
    // own buckets -- the SAME record reference appears in more than one
    // bucket array. The prior summary count (`reduce` summing bucket
    // lengths) double-counted such a record, and the prior render (mapping
    // each bucket independently) rendered that SAME record twice into the
    // DOM. A caller gives each rendered record a fixed id derived from its
    // own ref (e.g. `thermo-heading-${record.thermo_ref}`), so rendering it
    // twice produces two elements sharing one id -- this test's `renderRecord`
    // stands in for that with `data-testid`, which RTL's `getByTestId` also
    // refuses to resolve past a single match, catching the same class of bug.
    it("counts and renders a record naming MULTIPLE other conformers once, under a joint heading -- never double-counted or duplicated", () => {
        const multiLinked = { id: "x" }
        renderGroups({
            thisConformer: [],
            otherConformers: [
                { ref: "cg_two", label: "Conformer Group 2", records: [multiLinked] },
                { ref: "cg_three", label: "Conformer Group 3", records: [multiLinked] },
            ],
            noLink: [],
        })
        // Distinct-record count, not a sum of (possibly overlapping) bucket
        // lengths: ONE record, even though it's named by two buckets.
        expect(screen.getByText("1 record from other conformers")).toBeVisible()
        const details = document.querySelector(".conformer-attribution-other") as HTMLDetailsElement
        fireEvent.click(within(details).getByText("1 record from other conformers"))
        // Renders exactly once -- getByTestId throws on more than one match,
        // so this line itself is the duplicate-DOM-id guard.
        expect(within(details).getByTestId("record-x")).toBeVisible()
        // Named under a joint heading listing every group it actually
        // traces to, not silently attributed to only one of them.
        expect(within(details).getByRole("heading", { name: "From Conformer Group 2, Conformer Group 3" })).toBeVisible()
    })

    it("never renders a collapsed other-conformers disclosure when there are none", () => {
        renderGroups({ thisConformer: [{ id: "mine" }], otherConformers: [], noLink: [] })
        expect(screen.queryByText(/records? from other conformers/)).not.toBeInTheDocument()
        expect(document.querySelector(".conformer-attribution-other")).toBeNull()
    })

    it("omits the no-conformer-link group entirely when nothing falls into it", () => {
        renderGroups({ thisConformer: [{ id: "mine" }], otherConformers: [], noLink: [] })
        // An empty bucket used to print its heading and explanation for
        // nothing, directly under a card stating the conformer WAS derived --
        // which read as a contradiction. Assert the selected conformer's own
        // record still renders, so this cannot pass by the whole section
        // failing to mount.
        expect(screen.getByTestId("record-mine")).toBeInTheDocument()
        expect(screen.queryByText("No conformer link")).not.toBeInTheDocument()
        expect(screen.queryByText("No entry-level thermo record is deposited for this entry.")).not.toBeInTheDocument()
    })

    it("keeps a POPULATED no-conformer-link group at plain (non-emphasized) styling -- only the SELECTED conformer's state is emphasized", () => {
        renderGroups({ thisConformer: [{ id: "mine" }], otherConformers: [], noLink: [{ id: "unlinked" }] })
        expect(screen.getByText("No conformer link")).toBeInTheDocument()
        const heading = screen.getByText("No conformer link")
        expect(heading).not.toHaveClass("conformer-attribution-answer")
    })
})
