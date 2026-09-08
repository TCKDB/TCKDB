import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
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

    // Owner: "Evidence blocks should be expandable rather" -- built on the
    // shared `Disclosure` primitive, collapsed by default, so a reader
    // must open it before its rows become visible.
    describe("collapsible via the shared Disclosure primitive", () => {
        it("renders through a <details>/<summary>, closed by default", () => {
            const { container } = render(
                <EvidenceChecklist
                    heading="Evidence on this thing"
                    rows={[{ label: "Optimisation", value: "present", tone: "pill" }]}
                />,
            )
            const details = container.querySelector(".coverage-card details") as HTMLDetailsElement
            expect(details).not.toBeNull()
            expect(details.open).toBe(false)
            expect(container.querySelector(".coverage-checklist")).not.toBeVisible()
        })

        it("opens on a click of its own <summary>, revealing the rows -- exactly what a bare, always-open card already showed", () => {
            const { container } = render(
                <EvidenceChecklist
                    heading="Evidence on this thing"
                    rows={[{ label: "Optimisation", value: "present", tone: "pill" }]}
                />,
            )
            const details = container.querySelector(".coverage-card details") as HTMLDetailsElement
            fireEvent.click(details.querySelector("summary")!)
            expect(details.open).toBe(true)
            const checklist = container.querySelector(".coverage-checklist") as HTMLElement
            expect(checklist).toBeVisible()
            expect(ddFor(checklist, "Optimisation")).toHaveTextContent("present")
        })

        // Mutation table (b): dropping `defaultOpen={false}` (or bypassing
        // `Disclosure` to render the body unconditionally) would make this
        // fail -- the whole point of item 2 is that the card starts closed.
        it("MUTATION GUARD: a card would fail the closed-by-default assertion if it rendered open", () => {
            const { container } = render(
                <EvidenceChecklist heading="Evidence" rows={[{ label: "A", value: "present", tone: "pill" }]} />,
            )
            const details = container.querySelector("details") as HTMLDetailsElement
            expect(details.hasAttribute("open")).toBe(false)
        })
    })

    // The collapsed summary must not lose information: heading plus a
    // roll-up computed from each row's own `tone`, never a fixed string.
    describe("collapsed-summary roll-up, derived from row tones", () => {
        it("counts pill rows as present and pill-muted rows as absent", () => {
            const { container } = render(
                <EvidenceChecklist
                    heading="Evidence"
                    rows={[
                        { label: "A", value: "present", tone: "pill" },
                        { label: "B", value: "present", tone: "pill" },
                        { label: "C", value: "absent", tone: "pill-muted" },
                    ]}
                />,
            )
            const rollup = container.querySelector(".coverage-checklist-summary") as HTMLElement
            expect(rollup).not.toBeNull()
            expect(rollup).toHaveTextContent("2 present, 1 absent")
        })

        it("recomputes when the underlying rows change -- not a hardcoded string", () => {
            const { container, rerender } = render(
                <EvidenceChecklist heading="Evidence" rows={[{ label: "A", value: "present", tone: "pill" }]} />,
            )
            expect(container.querySelector(".coverage-checklist-summary")).toHaveTextContent("1 present, 0 absent")

            rerender(
                <EvidenceChecklist
                    heading="Evidence"
                    rows={[
                        { label: "A", value: "absent", tone: "pill-muted" },
                        { label: "B", value: "absent", tone: "pill-muted" },
                    ]}
                />,
            )
            expect(container.querySelector(".coverage-checklist-summary")).toHaveTextContent("0 present, 2 absent")
        })

        it("falls back to a plain row count when no row carries a tone at all (a count list, not a status checklist)", () => {
            const { container } = render(
                <EvidenceChecklist
                    heading="Evidence"
                    rows={[
                        { label: "Optimisation", value: "2 of 2 observations" },
                        { label: "Frequency", value: "1 of 2 observations" },
                    ]}
                />,
            )
            expect(container.querySelector(".coverage-checklist-summary")).toHaveTextContent("2 rows")
        })

        // Mutation table (c): a summary that stops rendering the computed
        // roll-up (e.g. reverting to just the bare heading) fails this.
        it("MUTATION GUARD: the summary carries digits from the roll-up, not just the heading text", () => {
            const { container } = render(
                <EvidenceChecklist
                    heading="Evidence on this thing"
                    rows={[
                        { label: "A", value: "present", tone: "pill" },
                        { label: "B", value: "absent", tone: "pill-muted" },
                    ]}
                />,
            )
            const summary = container.querySelector(".coverage-card summary") as HTMLElement
            expect(summary.textContent).toMatch(/\d+ present, \d+ absent/)
        })
    })

    // Item 3 (reaction-entry page): a pill asserting PRESENCE can opt into
    // a same-page link; an absence never can, even if a caller passes `to`
    // on a muted row by mistake.
    describe("row links -- only a presence-asserting (tone: 'pill') row may ever link", () => {
        it("renders a pill row's value as a real <a> when tone is 'pill' and 'to' is given", () => {
            const { container } = render(
                <EvidenceChecklist
                    heading="Evidence"
                    rows={[{ label: "Kinetics records", value: "1 deposited", tone: "pill", to: "#kinetics-heading" }]}
                />,
            )
            const link = container.querySelector(".coverage-checklist a") as HTMLAnchorElement
            expect(link).not.toBeNull()
            expect(link).toHaveAttribute("href", "#kinetics-heading")
            expect(link).toHaveClass("value-pill")
            expect(link).not.toHaveClass("value-pill--muted")
            expect(link).toHaveTextContent("1 deposited")
        })

        it("a plain 'pill' row with no 'to' stays a <span>, not a link", () => {
            const { container } = render(
                <EvidenceChecklist heading="Evidence" rows={[{ label: "A", value: "present", tone: "pill" }]} />,
            )
            expect(container.querySelector(".coverage-checklist a")).toBeNull()
            expect(container.querySelector(".coverage-checklist .value-pill")?.tagName).toBe("SPAN")
        })

        // MANDATORY per the mutation table (a is the participants-table
        // case; this is the checklist-component analogue): "none
        // deposited"/absence never becomes a link even when a caller
        // supplies 'to' on a muted row -- a link promises a destination,
        // an absence has none.
        it("MUTATION GUARD: 'to' on a pill-muted row is silently ignored -- an absence never links", () => {
            const { container } = render(
                <EvidenceChecklist
                    heading="Evidence"
                    rows={[{ label: "Kinetics records", value: "none deposited", tone: "pill-muted", to: "#kinetics-heading" }]}
                />,
            )
            expect(container.querySelector(".coverage-checklist a")).toBeNull()
            const pill = container.querySelector(".coverage-checklist .value-pill") as HTMLElement
            expect(pill.tagName).toBe("SPAN")
            expect(pill).toHaveClass("value-pill--muted")
        })

        it("'to' on a toneless (plain-text) row is also ignored -- never a link", () => {
            const { container } = render(
                <EvidenceChecklist heading="Evidence" rows={[{ label: "Count", value: "3 of 4", to: "#somewhere" }]} />,
            )
            expect(container.querySelector(".coverage-checklist a")).toBeNull()
        })
    })
})
