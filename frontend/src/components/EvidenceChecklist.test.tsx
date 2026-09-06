import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { EvidenceChecklist } from "./EvidenceChecklist"

afterEach(cleanup)

/** Reads the `<dd>` text for a `<dt>` term inside `container`. */
function ddFor(container: HTMLElement, term: string): HTMLElement {
    const dt = Array.from(container.querySelectorAll("dt")).find((el) => el.textContent === term)
    if (!dt) throw new Error(`No <dt> with text "${term}" found`)
    return dt.nextElementSibling as HTMLElement
}

describe("EvidenceChecklist", () => {
    it("renders the heading as a .t-label inside a .card.card--derived.coverage-card", () => {
        const { container } = render(
            <EvidenceChecklist heading="Evidence on this thing" rows={[{ label: "Optimisation", value: "present" }]} />,
        )
        const card = container.querySelector(".card.card--derived.coverage-card") as HTMLElement
        expect(card).not.toBeNull()
        const heading = within(card).getByText("Evidence on this thing")
        expect(heading.tagName).toBe("SPAN")
        expect(heading).toHaveClass("t-label")
    })

    // The marker a page-level test uses to confirm "this card came from the
    // shared component" without depending on any one page's own class names
    // or copy (see e.g. `CalculationDetailPage.test.tsx`'s own use of it).
    it('marks its root with data-component="evidence-checklist"', () => {
        const { container } = render(<EvidenceChecklist heading="Evidence" rows={[]} />)
        const card = container.querySelector(".coverage-card") as HTMLElement
        expect(card).toHaveAttribute("data-component", "evidence-checklist")
    })

    it("renders rows as a single-column .kv-list.coverage-checklist, label above value", () => {
        const { container } = render(
            <EvidenceChecklist
                heading="Evidence"
                rows={[
                    { label: "Optimisation", value: "present" },
                    { label: "Frequency", value: "absent" },
                ]}
            />,
        )
        const list = container.querySelector("dl.kv-list.coverage-checklist") as HTMLElement
        expect(list).not.toBeNull()
        expect(ddFor(list, "Optimisation")).toHaveTextContent("present")
        expect(ddFor(list, "Frequency")).toHaveTextContent("absent")
        // Label above value -- a real <dt>/<dd> pair per row, not a run-on
        // sentence sharing one element (the owner report this whole
        // component exists to fix).
        const rows = list.querySelectorAll(":scope > div")
        expect(rows).toHaveLength(2)
        for (const row of rows) {
            expect(row.querySelector("dt")).not.toBeNull()
            expect(row.querySelector("dd")).not.toBeNull()
        }
    })

    it("renders a value with no tone as plain text -- no pill wrapper", () => {
        const { container } = render(
            <EvidenceChecklist heading="Evidence" rows={[{ label: "Geometry validation", value: "not applicable" }]} />,
        )
        const list = container.querySelector(".coverage-checklist") as HTMLElement
        const dd = ddFor(list, "Geometry validation")
        expect(dd).toHaveTextContent("not applicable")
        expect(dd.querySelector(".value-pill")).toBeNull()
    })

    it('tone: "pill" renders the value inside a .value-pill span (not muted)', () => {
        const { container } = render(
            <EvidenceChecklist heading="Evidence" rows={[{ label: "Optimisation", value: "present", tone: "pill" }]} />,
        )
        const list = container.querySelector(".coverage-checklist") as HTMLElement
        const dd = ddFor(list, "Optimisation")
        const pill = dd.querySelector("span") as HTMLElement
        expect(pill).not.toBeNull()
        expect(pill).toHaveClass("value-pill")
        expect(pill).not.toHaveClass("value-pill--muted")
        expect(pill).toHaveTextContent("present")
    })

    it('tone: "pill-muted" renders the value inside a .value-pill.value-pill--muted span (both classes)', () => {
        const { container } = render(
            <EvidenceChecklist heading="Evidence" rows={[{ label: "Optimisation", value: "absent", tone: "pill-muted" }]} />,
        )
        const list = container.querySelector(".coverage-checklist") as HTMLElement
        const dd = ddFor(list, "Optimisation")
        const pill = dd.querySelector("span") as HTMLElement
        expect(pill).not.toBeNull()
        expect(pill).toHaveClass("value-pill")
        expect(pill).toHaveClass("value-pill--muted")
        expect(pill).toHaveTextContent("absent")
    })

    it("renders the note as a .note paragraph when given, and renders nothing when omitted", () => {
        const { container, rerender } = render(
            <EvidenceChecklist heading="Evidence" rows={[]} note="A short explanatory line." />,
        )
        expect(screen.getByText("A short explanatory line.")).toHaveClass("note")
        expect(container.querySelector("p.note")?.tagName).toBe("P")

        rerender(<EvidenceChecklist heading="Evidence" rows={[]} />)
        expect(container.querySelector("p.note")).toBeNull()
    })

    it("supports a ReactNode note (e.g. a conditional trailing sentence)", () => {
        render(
            <EvidenceChecklist
                heading="Evidence"
                rows={[]}
                note={<>First sentence.<> Second sentence.</></>}
            />,
        )
        expect(screen.getByText(/First sentence\. Second sentence\./)).toHaveClass("note")
    })
})
