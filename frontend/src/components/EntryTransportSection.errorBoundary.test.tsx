import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import type { TransportRecord } from "../api/transportApi"
import { LazyRowBody } from "./EntryTransportSection"
import { SectionErrorBoundary } from "./SectionErrorBoundary"

afterEach(() => cleanup())

// See the identical test's docstring in
// `EntryStatmechSection.errorBoundary.test.tsx` — same pattern, same fix,
// same reason a naive inline `{children(record, data)}` call would not
// actually have been caught.
const record = { transport: { transport_ref: "trn_boom" } } as unknown as TransportRecord

describe("EntryTransportSection per-row error isolation", () => {
    it("a throw inside one row's render is caught by that row's own boundary — sibling rows are unaffected", () => {
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
        expect(screen.getByText("row two data: fine")).toBeVisible()
        expect(screen.queryByText("row two fallback")).not.toBeInTheDocument()
    })

    it("documents why calling the render prop inline inside <SectionErrorBoundary> is NOT actually caught", () => {
        expect(() => render(
            <SectionErrorBoundary fallback={<p>fallback</p>}>
                {(() => { throw new Error("boom") })()}
            </SectionErrorBoundary>,
        )).toThrow("boom")
    })
})
