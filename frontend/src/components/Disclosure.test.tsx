import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
// `Disclosure.tsx` no longer imports `design-system.css` itself (post-
// review, PR D -- see that file's own comment: the component's production
// callers all already get it via `index.css`'s single global `@import`).
// This test file DOES need it, though: the "never uppercases" test below
// asserts a real computed style, and without an actual `.disclosure >
// summary` rule loaded, `getComputedStyle` would fall back to the
// browser's own initial value for `text-transform` ("none") regardless of
// what the rule says -- passing that assertion without ever having
// exercised the rule at all.
import "../design-system.css"
import { Disclosure } from "./Disclosure"

afterEach(cleanup)

describe("Disclosure", () => {
    it("renders the summary text and an optional trailing count", () => {
        render(<Disclosure summary="References" count={4}><p>body</p></Disclosure>)
        const summary = screen.getByText("References", { exact: false })
        expect(summary).toHaveTextContent("References (4)")
    })

    it("renders the summary with no count when count is omitted", () => {
        render(<Disclosure summary="Details"><p>body</p></Disclosure>)
        expect(screen.getByText("Details")).toHaveTextContent("Details")
        expect(screen.queryByText(/\(/)).toBeNull()
    })

    it("never uppercases the summary text -- sentence case, not the retired kicker/label transform", () => {
        render(<Disclosure summary="References"><p>body</p></Disclosure>)
        const summary = screen.getByText("References", { exact: false })
        const style = window.getComputedStyle(summary)
        expect(style.textTransform).not.toBe("uppercase")
    })

    it("starts closed by default and opens on click, toggling the [open] attribute the chevron rotation hooks off of", async () => {
        const user = userEvent.setup()
        render(<Disclosure summary="References" count={2}><p>ref body</p></Disclosure>)
        const details = screen.getByText("References", { exact: false }).closest("details") as HTMLDetailsElement
        expect(details.open).toBe(false)

        await user.click(screen.getByText("References", { exact: false }))
        expect(details.open).toBe(true)

        await user.click(screen.getByText("References", { exact: false }))
        expect(details.open).toBe(false)
    })

    it("respects defaultOpen", () => {
        render(<Disclosure summary="References" defaultOpen><p>ref body</p></Disclosure>)
        const details = screen.getByText("References", { exact: false }).closest("details") as HTMLDetailsElement
        expect(details.open).toBe(true)
    })

    // Neither jsdom nor @testing-library/user-event implements the UA
    // "activation behaviour" that translates a real browser's Enter/Space
    // keypress on a focused `<summary>` into a click (MEASURED: jsdom's
    // own `HTMLDetailsElement`/`HTMLElement` implementation wires up
    // click-driven toggling -- see the passing click test above -- but
    // has no keydown handling at all; user-event's keydown behavior table
    // covers arrow keys, Tab, Backspace/Delete and Ctrl+A only, nothing
    // that synthesizes a click from Enter/Space). What CAN be checked
    // without a real browser is the two facts a native `<summary>`'s
    // keyboard activation depends on: it is a genuinely focusable,
    // un-suppressed `<summary>` element (not a fake clickable `<div>`,
    // and not `tabindex="-1"`'d out of the tab order), and `.click()` --
    // the exact call a browser's Enter/Space handling performs on it --
    // does toggle `open`.
    it("keyboard-activates like any native <summary>: focusable in the tab order, and .click() (what a browser's Enter/Space translates to) toggles it", async () => {
        const user = userEvent.setup()
        render(<Disclosure summary="References"><p>ref body</p></Disclosure>)
        const summary = screen.getByText("References", { exact: false })
        const details = summary.closest("details") as HTMLDetailsElement

        expect(summary.tagName).toBe("SUMMARY")
        expect(summary).not.toHaveAttribute("tabindex", "-1")

        await user.tab()
        expect(summary).toHaveFocus()

        expect(details.open).toBe(false)
        summary.click()
        expect(details.open).toBe(true)
    })

    it("calls onToggle with the new open state", async () => {
        const user = userEvent.setup()
        const onToggle = vi.fn()
        render(<Disclosure summary="References" onToggle={onToggle}><p>ref body</p></Disclosure>)
        await user.click(screen.getByText("References", { exact: false }))
        expect(onToggle).toHaveBeenCalledWith(true)
    })

    it("applies the given id to the <details> element", () => {
        render(<Disclosure summary="References" id="refs-1"><p>ref body</p></Disclosure>)
        expect(document.getElementById("refs-1")?.tagName).toBe("DETAILS")
    })

    it("adds a caller className alongside the base .disclosure class, not in place of it", () => {
        render(<Disclosure summary="References" className="refs-disclosure"><p>ref body</p></Disclosure>)
        const details = screen.getByText("References", { exact: false }).closest("details") as HTMLDetailsElement
        expect(details.className.split(" ")).toEqual(expect.arrayContaining(["disclosure", "refs-disclosure"]))
    })

    // Post-review (PR D): a caller whose disclosure sits beside another
    // heading it does not itself render (`SpeciesOverviewPage.tsx`'s
    // per-state-group `EntryStateGroup`, associating "Deposited records"
    // back to that group's own `<h3>`) needs a way to set attributes on the
    // `<summary>` element itself -- `className`/`id` above reach the outer
    // `<details>`, not the summary a screen-reader user actually lands on.
    it("spreads summaryProps onto the <summary> element", () => {
        render(
            <Disclosure summary="Deposited records" summaryProps={{ "aria-describedby": "group-heading-1" }}>
                <p>body</p>
            </Disclosure>,
        )
        const summary = screen.getByText("Deposited records", { exact: false })
        expect(summary.tagName).toBe("SUMMARY")
        expect(summary).toHaveAttribute("aria-describedby", "group-heading-1")
    })

    it("still renders the summary text and count correctly when summaryProps is also given -- the prop adds attributes, it does not replace the content", () => {
        render(
            <Disclosure count={3} summary="Deposited records" summaryProps={{ "aria-describedby": "group-heading-1" }}>
                <p>body</p>
            </Disclosure>,
        )
        const summary = screen.getByText("Deposited records", { exact: false })
        expect(summary).toHaveTextContent("Deposited records (3)")
        expect(summary).toHaveAttribute("aria-describedby", "group-heading-1")
    })

    it("omitting summaryProps still renders a plain <summary> with no stray attribute", () => {
        render(<Disclosure summary="References"><p>body</p></Disclosure>)
        const summary = screen.getByText("References", { exact: false })
        expect(summary).not.toHaveAttribute("aria-describedby")
    })
})
