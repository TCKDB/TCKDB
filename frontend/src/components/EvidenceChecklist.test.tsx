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

    // Independent review: `evidence-checklist.css` used to carry a
    // `.coverage-card > .t-label { display: block }` rule guarding a ~4px
    // "heading renders inline, card shrinks" regression from BEFORE this
    // card was collapsible, when the heading span and the `<dl>` sat as
    // direct, adjacent siblings inside `.coverage-card` (the heading's own
    // box height affected where the `<dl>` sat directly below it). Now
    // RETIRED: the heading lives inside `Disclosure`'s own `<summary>`,
    // and the `<dl>` lives inside a SEPARATE `.disclosure-body` sibling of
    // that `<summary>` -- they are no longer adjacent siblings at all, so
    // the heading's own `display` value cannot affect the `<dl>`'s
    // position any more, regardless of what it computes to (MEASURED:
    // still `inline`, same UA default a bare `<span>` always had -- no
    // blockification rescues it, and none is needed). This is pinned
    // structurally below (heading and checklist are proven to sit in
    // different disclosure regions) rather than as a `display` assertion
    // that would just restate an unchanged UA default.
    it("the heading (inside <summary>) and the checklist <dl> (inside .disclosure-body) are separate siblings, not adjacent boxes whose heights interact", () => {
        const { container } = render(
            <EvidenceChecklist heading="Evidence on this thing" rows={[{ label: "Optimisation", value: "present" }]} />,
        )
        const summary = container.querySelector(".coverage-card summary") as HTMLElement
        const body = container.querySelector(".coverage-card .disclosure-body") as HTMLElement
        expect(within(summary).getByText("Evidence on this thing")).toBeInTheDocument()
        expect(body.querySelector(".coverage-checklist")).not.toBeNull()
        // The heading is NOT inside the body the checklist lives in.
        expect(summary.contains(body)).toBe(false)
        expect(body.contains(summary)).toBe(false)
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
        // fail.
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

        // Independent review, this branch: an EARLIER version of this
        // component fell back to a bare `"N rows"` count when no row
        // carried a tone -- MEASURED on live data as actively misleading,
        // since that number is the CARD'S OWN fixed row count (constant
        // per card shape, e.g. always 6 for the reaction-entry review
        // card), not a fact about the archive. It restated a different
        // question's answer as if it answered the heading's own: a
        // reaction entry with 4 real joined records collapsed to "6
        // rows"; two conformer groups with opposite coverage (every stage
        // fully covered vs. none at all) BOTH collapsed to "3 rows",
        // indistinguishable from each other. There is no numeric fallback
        // any more -- see the two describe blocks below for what replaced
        // it.
        describe("no numeric row-count fallback -- untoned rows need a caller-supplied `summary`, or the card opens instead", () => {
            it("a caller-supplied `summary` is used when no tone-derived roll-up exists", () => {
                const { container } = render(
                    <EvidenceChecklist
                        heading="Joined-record review counts"
                        summary="4 joined records"
                        rows={[
                            { label: "Approved", value: 1 },
                            { label: "Under review", value: 1 },
                            { label: "Not reviewed", value: 1 },
                            { label: "Deprecated", value: 0 },
                            { label: "Rejected", value: 0 },
                            { label: "Total joined records", value: 4 },
                        ]}
                    />,
                )
                const rollup = container.querySelector(".coverage-checklist-summary") as HTMLElement
                expect(rollup).toHaveTextContent("4 joined records")
                // Still collapsed by default -- a real summary was supplied.
                const details = container.querySelector("details") as HTMLDetailsElement
                expect(details.open).toBe(false)
            })

            // MANDATORY guard (independent review): fails if a future
            // regression ever makes the ROW COUNT the only number in a
            // collapsed summary again, toned or not.
            it("MUTATION GUARD: the collapsed summary's only number is never the row count -- it must come from the data, not the row list's own length", () => {
                const distinctFromRowCount = "4 joined records" // rows.length below is 6
                const { container } = render(
                    <EvidenceChecklist
                        heading="Joined-record review counts"
                        summary={distinctFromRowCount}
                        rows={[
                            { label: "Approved", value: 1 },
                            { label: "Under review", value: 1 },
                            { label: "Not reviewed", value: 1 },
                            { label: "Deprecated", value: 0 },
                            { label: "Rejected", value: 0 },
                            { label: "Total joined records", value: 4 },
                        ]}
                    />,
                )
                const rollup = container.querySelector(".coverage-checklist-summary") as HTMLElement
                const digits = rollup.textContent?.match(/\d+/g) ?? []
                // The row list has 6 entries -- the roll-up's own leading
                // number must NOT be that count.
                expect(digits[0]).not.toBe("6")
                expect(rollup).toHaveTextContent("4 joined records")
            })

            it("renders the card OPEN by default when rows carry no tone and the caller supplies no `summary` -- never collapses behind nothing", () => {
                const { container } = render(
                    <EvidenceChecklist
                        heading="Evidence on this conformer group"
                        rows={[
                            { label: "Optimisation", value: "2 of 2 observations" },
                            { label: "Frequency", value: "1 of 2 observations" },
                        ]}
                    />,
                )
                const details = container.querySelector("details") as HTMLDetailsElement
                expect(details.open).toBe(true)
                expect(container.querySelector(".coverage-checklist")).toBeVisible()
                // No roll-up span at all -- nothing to show, so nothing shown.
                expect(container.querySelector(".coverage-checklist-summary")).toBeNull()
            })

            // Mutation table (d): restoring the old `"N rows"` fallback (or
            // otherwise rendering a numeric summary with no real `summary`
            // and no toned rows) must turn this red.
            it("MUTATION GUARD: no `summary` and no toned rows means no numeric roll-up and an OPEN card", () => {
                const { container } = render(
                    <EvidenceChecklist
                        heading="Evidence"
                        rows={[
                            { label: "Optimisation", value: "2 of 2 observations" },
                            { label: "Frequency", value: "1 of 2 observations" },
                            { label: "Single point", value: "0 of 2 observations" },
                        ]}
                    />,
                )
                const details = container.querySelector("details") as HTMLDetailsElement
                expect(details.hasAttribute("open")).toBe(true)
                const rollup = container.querySelector(".coverage-checklist-summary")
                expect(rollup).toBeNull()
            })
        })
    })

    // Item 3 (reaction-entry page): a row the caller marked `tone: "pill"`
    // can opt into a same-page link. What this component itself enforces
    // is narrower than "presence links, absence doesn't" -- `tone` is the
    // CALLER's own claim, not something derived from `value` -- so the
    // real, testable invariant is "a `pill-muted`/toneless row never
    // links, even if a caller passes `to` on one" (see this component's
    // own docstring for the exact wording).
    describe("row links -- only a row the caller marked tone: 'pill' may ever link", () => {
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

        // MANDATORY per the mutation table: `to` on a `pill-muted` row is
        // silently ignored by THIS component, regardless of what a caller
        // passes -- the enforcement lives here, not in caller discipline.
        it("MUTATION GUARD: 'to' on a pill-muted row is silently ignored, even though the caller supplied it", () => {
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
