import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import type { StatmechRecord } from "../api/statmechApi"
import { LazyRowBody } from "./EntryStatmechSection"
import { SectionErrorBoundary } from "./SectionErrorBoundary"

afterEach(() => cleanup())

// A throw here is not reachable through any real data path today — every
// field a lazy-section renderer touches is zod-validated before the record
// ever reaches "ready" state (see `api/statmechApi.ts`), so a malformed
// value is rejected as `malformed` well before render. This file tests the
// DEFENSIVE PATTERN itself: if a renderer ever does throw (a future field,
// a parsing edge case zod's schema does not catch), does the failure stay
// scoped to that one row?
const record = { statmech: { statmech_ref: "sm_boom" } } as unknown as StatmechRecord

describe("EntryStatmechSection per-row error isolation", () => {
    it("a throw inside one row's render is caught by that row's own boundary — sibling rows, wrapped in their own boundaries, are unaffected", () => {
        render(
            <div>
                <SectionErrorBoundary fallback={<p role="alert">row one fallback</p>}>
                    <LazyRowBody record={record} data={null} render={() => { throw new Error("boom") }} />
                </SectionErrorBoundary>
                <SectionErrorBoundary fallback={<p role="alert">row two fallback</p>}>
                    <LazyRowBody record={record} data="fine" render={(_row, data) => <p>row two data: {String(data)}</p>} />
                </SectionErrorBoundary>
            </div>,
        )
        expect(screen.getByRole("alert")).toHaveTextContent("row one fallback")
        // The sibling row — a stand-in for a second statmech record's own
        // disclosure row in the same lazy section — renders normally. This
        // is the fix for the slice-4-shaped defect the review caught: six
        // lazy sections previously sat under only the whole-tab boundary,
        // so one bad row would have destroyed every record card, the
        // review summary, and all five sibling sections at once.
        expect(screen.getByText("row two data: fine")).toBeVisible()
        expect(screen.queryByText("row two fallback")).not.toBeInTheDocument()
    })

    it("documents why calling the render prop inline inside <SectionErrorBoundary> — the first version of this fix — is NOT actually caught", () => {
        // {children(record, data)} evaluates synchronously while the PARENT
        // component is still constructing its own JSX tree, before
        // SectionErrorBoundary is ever mounted — so the throw propagates
        // straight past `render()`. LazyRowBody exists specifically to
        // defer that call into a real descendant component's own render,
        // where the boundary can actually see it. This test is the
        // regression guard for that exact near-miss.
        expect(() => render(
            <SectionErrorBoundary fallback={<p>fallback</p>}>
                {(() => { throw new Error("boom") })()}
            </SectionErrorBoundary>,
        )).toThrow("boom")
    })
})
