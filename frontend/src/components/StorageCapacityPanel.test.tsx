import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StorageCapacityPanel } from "./StorageCapacityPanel"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const CAPACITY = "/api/v1/admin/artifact-storage/capacity"

function capacityIs(body: Record<string, unknown>) {
    server.use(http.get(CAPACITY, () => HttpResponse.json(body)))
}

describe("when nothing is outstanding", () => {
    it("says no refusal is recorded, and claims nothing about free space", async () => {
        /**
         * The endpoint reports the head of an append-only log, not a probe
         * it just ran -- there is no timestamp in this case because no
         * measurement was taken. Wording like "healthy" or "checked just
         * now" would be a freshness claim the archive never made, which is
         * the failure this surface is careful about everywhere else.
         */
        capacityIs({ storage_full: false, storage_full_observed_at: null, s3_code: null, refused_bytes: null })
        render(<StorageCapacityPanel />)

        const note = await screen.findByRole("status")
        expect(note).toHaveTextContent(/no storage-full refusal recorded/i)
        expect(note.textContent).toMatch(/not a disk check/i)
        expect(note.textContent).not.toMatch(/healthy|checked just now/i)
        // Nothing to clear, so nothing to clear it with.
        expect(screen.queryByRole("button", { name: /record as resolved/i })).not.toBeInTheDocument()
    })
})

describe("when the store refused a write", () => {
    const FULL = {
        storage_full: true,
        storage_full_observed_at: "2026-09-14T02:15:33",
        s3_code: "QuotaExceeded",
        refused_bytes: 5 * 1024 ** 3,
    }

    it("shows what was refused, in units an operator can compare against du", async () => {
        capacityIs(FULL)
        render(<StorageCapacityPanel />)

        expect(await screen.findByRole("alert")).toHaveTextContent(/refused a write for want of room/i)
        expect(screen.getByText("QuotaExceeded")).toBeInTheDocument()
        expect(screen.getByText("5.0 GiB")).toBeInTheDocument()
        expect(screen.getByText("2026-09-14 02:15:33")).toBeInTheDocument()
    })

    it("wraps each label/value pair, which .kv-list requires and does not state", async () => {
        /**
         * `.kv-list` is `display: grid` with `auto-fit` columns, so a bare
         * <dt>/<dd> are two SEPARATE grid items that flow independently.
         * Without a wrapping <div> per pair this rendered "Recorded" above
         * `QuotaExceeded` and "Store said" above "5.0 GiB" -- every label
         * beside the wrong value. Every other caller in the app wraps
         * (`CalculationDetailPage.tsx:830`); the contract lives in usage,
         * not in the stylesheet, so nothing enforced it.
         *
         * jsdom cannot see the layout, but it CAN see the structure the
         * layout depends on, which is the checkable half.
         */
        capacityIs(FULL)
        render(<StorageCapacityPanel />)
        await screen.findByText("QuotaExceeded")

        const list = document.querySelector("dl.kv-list")
        expect(list).not.toBeNull()
        const terms = [...list!.querySelectorAll("dt")]
        expect(terms).toHaveLength(3)
        for (const term of terms) {
            // The pair's own wrapper, not the <dl> itself.
            expect(term.parentElement?.tagName).toBe("DIV")
            expect(term.parentElement?.parentElement).toBe(list)
            expect(term.parentElement?.querySelector("dd")).not.toBeNull()
        }
    })

    it("will not send an empty reason, and says why one is wanted", async () => {
        capacityIs(FULL)
        // No POST handler registered: with `onUnhandledRequest: "error"` a
        // request would fail the test, so this asserts the click sends
        // nothing rather than merely that a message appeared.
        render(<StorageCapacityPanel />)

        await userEvent.click(await screen.findByRole("button", { name: /record as resolved/i }))

        const alerts = await screen.findAllByRole("alert")
        expect(alerts.some((node) => /give a reason/i.test(node.textContent ?? ""))).toBe(true)
    })

    it("posts the reason and renders the state the server returns", async () => {
        capacityIs(FULL)
        let posted: unknown = null
        server.use(http.post(`${CAPACITY}/clear`, async ({ request }) => {
            posted = await request.json()
            return HttpResponse.json({
                storage_full: false, storage_full_observed_at: null, s3_code: null, refused_bytes: null,
            })
        }))

        render(<StorageCapacityPanel />)
        await userEvent.type(
            await screen.findByLabelText(/what changed/i), "pruned 40 GiB of superseded bundles")
        await userEvent.click(screen.getByRole("button", { name: /record as resolved/i }))

        expect(await screen.findByRole("status")).toHaveTextContent(/no storage-full refusal recorded/i)
        expect(posted).toEqual({ reason: "pruned 40 GiB of superseded bundles" })
    })

    it("tells the operator what happens if the store is still full", async () => {
        /**
         * Clearing rests on an assertion rather than a measurement. Someone
         * about to assert something should be able to read the consequence
         * of asserting it wrongly, without having to find the runbook.
         */
        capacityIs(FULL)
        render(<StorageCapacityPanel />)
        await screen.findByRole("button", { name: /record as resolved/i })

        expect(document.body.textContent).toMatch(/appends to the log; it does not edit the refusal/i)
        expect(document.body.textContent).toMatch(/records a new refusal/i)
    })

    it("keeps the refusal on screen when the clear fails", async () => {
        capacityIs(FULL)
        server.use(http.post(`${CAPACITY}/clear`, () =>
            HttpResponse.json({ code: "http_500", detail: "boom", context: {} }, { status: 500 })))

        render(<StorageCapacityPanel />)
        await userEvent.type(await screen.findByLabelText(/what changed/i), "tried")
        await userEvent.click(screen.getByRole("button", { name: /record as resolved/i }))

        const alerts = await screen.findAllByRole("alert")
        expect(alerts.some((node) => /boom/i.test(node.textContent ?? ""))).toBe(true)
        // The condition is still outstanding; the panel must not imply otherwise.
        expect(screen.queryByRole("status")).not.toBeInTheDocument()
        expect(screen.getByText("QuotaExceeded")).toBeInTheDocument()
    })
})

describe("when the capacity read itself fails", () => {
    it("reports that, rather than rendering an all-clear", async () => {
        server.use(http.get(CAPACITY, () =>
            HttpResponse.json({ code: "http_503", detail: "unavailable", context: {} }, { status: 503 })))
        render(<StorageCapacityPanel />)

        expect(await screen.findByRole("alert")).toHaveTextContent(/unavailable/i)
        expect(screen.queryByRole("status")).not.toBeInTheDocument()
    })
})
