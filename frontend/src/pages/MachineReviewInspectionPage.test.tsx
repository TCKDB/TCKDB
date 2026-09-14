import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import MachineReviewInspectionPage from "./MachineReviewInspectionPage"

/**
 * The record-summaries table used to carry a column headed `record_ref` whose
 * value was a database row id. `record_ref` means the *public* ref everywhere
 * else in this API, so the page promised a handle and delivered a row number.
 *
 * It now shows `record_public_ref` -- the identifier the archive is addressed
 * by -- and `record_id`, which an admin-only surface may carry. These tests pin
 * which value lands in which column, and that a record with no public ref shows
 * nothing rather than falling back to its id.
 */

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const SUMMARY = {
    status: "machine_screened_warning",
    highest_severity: "warning",
    findings_count: 1,
    model: "fake_test/simple-v1",
    provider: "FakeLLMPrecheckProvider",
    reviewed_at: "2026-09-14T10:00:00Z",
    submission_id: 7,
}

const PUBLIC_REF = "spc_vu7cuk4s37szxaudjpf355tqda"

function serve(record: Record<string, unknown>) {
    server.use(
        http.get("/api/v1/admin/submissions/7/machine-review-inspection", () =>
            HttpResponse.json({
                submission_id: 7,
                record_summaries: [record],
                unmapped_findings_count: 0,
                mapping_warnings: [],
                parse_warnings: [],
                source_audit_event_ids: [11],
            }),
        ),
    )
}

async function inspect() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const user = userEvent.setup()
    render(
        <QueryClientProvider client={client}>
            <MachineReviewInspectionPage />
        </QueryClientProvider>,
    )
    await user.type(screen.getByLabelText(/submission_id/), "7")
    await user.click(screen.getByRole("button", { name: "Inspect" }))
}

/** The record row's cell texts, in column order. */
async function recordCells(): Promise<string[]> {
    const table = (await screen.findAllByRole("table")).at(-1)!
    const bodyRow = within(table).getAllByRole("row").at(-1)!
    return within(bodyRow).getAllByRole("cell").map((c) => (c.textContent ?? "").trim())
}

describe("the record summaries table", () => {
    it("shows the public ref and the row id in their own columns", async () => {
        serve({
            record_type: "species",
            record_public_ref: PUBLIC_REF,
            record_id: 431,
            latest_summary: SUMMARY,
            all_record_reviews_count: 1,
        })
        await inspect()

        // By position, not just presence: a swap leaves both strings on the
        // page and would satisfy any "is it rendered" check. The two values are
        // deliberately unlike each other so a swap is visible.
        const cells = await recordCells()
        expect(cells[0]).toBe("species")
        expect(cells[1]).toBe(PUBLIC_REF)
        expect(cells[2]).toBe("431")
    })

    it("headings name what the columns hold", async () => {
        serve({
            record_type: "species",
            record_public_ref: PUBLIC_REF,
            record_id: 431,
            latest_summary: SUMMARY,
            all_record_reviews_count: 1,
        })
        await inspect()

        const table = (await screen.findAllByRole("table")).at(-1)!
        const headings = within(table)
            .getAllByRole("columnheader")
            .map((h) => h.textContent)

        expect(headings).toContain("record_public_ref")
        expect(headings).toContain("record_id")
        // The old name promised a public ref and delivered a row id.
        expect(headings).not.toContain("record_ref")
        // Dropped rather than renamed: it was always `String(record_id)`.
        expect(headings).not.toContain("record_match_key")
    })

    it("a record that cannot be named shows a dash, never its row id", async () => {
        /**
         * `applied_energy_correction` has no `public_ref` column, so the
         * backend answers `null`. Falling back to `record_id` would put a row
         * id under a heading that says "public ref" -- the exact defect this
         * page had.
         */
        serve({
            record_type: "applied_energy_correction",
            record_public_ref: null,
            record_id: 88,
            latest_summary: SUMMARY,
            all_record_reviews_count: 1,
        })
        await inspect()

        const cells = await recordCells()
        expect(cells[1]).not.toBe("88")
        expect(cells[1]).not.toBe("")
        expect(cells[2]).toBe("88")
    })
})
