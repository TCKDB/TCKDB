import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import MachineReviewInspectionPage from "./MachineReviewInspectionPage"

/**
 * The record-summaries table carries two identifiers that are easy to confuse
 * and were confused: `record_match_key` is the private key the machine-review
 * stack grouped the finding by (a stringified database row id in the audit
 * path), and `record_public_ref` is the handle the archive is addressed by.
 *
 * Until 2026-09-14 only the first was published, under the name `record_ref` --
 * which is what the public read surfaces call a *public* ref. So this page
 * rendered a row id beneath a heading that promised a handle. These tests pin
 * which value lands in which column, which is the one thing a rename of this
 * kind can silently get wrong.
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

function inspectionBody(record: Record<string, unknown>) {
    return {
        submission_id: 7,
        record_summaries: [record],
        unmapped_findings_count: 0,
        mapping_warnings: [],
        parse_warnings: [],
        source_audit_event_ids: [11],
    }
}

function renderPage() {
    const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
    })
    return render(
        <QueryClientProvider client={client}>
            <MachineReviewInspectionPage />
        </QueryClientProvider>,
    )
}

async function inspect() {
    const user = userEvent.setup()
    renderPage()
    await user.type(screen.getByLabelText(/submission_id/), "7")
    await user.click(screen.getByRole("button", { name: "Inspect" }))
    return user
}

/** The record row, as a list of cell texts in column order. */
async function recordCells(): Promise<string[]> {
    const table = (await screen.findAllByRole("table")).at(-1)!
    const bodyRow = within(table).getAllByRole("row").at(-1)!
    return within(bodyRow).getAllByRole("cell").map((c) => c.textContent ?? "")
}

describe("the record summaries table", () => {
    it("puts the public ref and the match key in their own columns", async () => {
        server.use(
            http.get(
                "/api/v1/admin/submissions/7/machine-review-inspection",
                () => HttpResponse.json(inspectionBody({
                    record_type: "species",
                    record_match_key: "431",
                    record_public_ref: "spc_vu7cuk4s37szxaudjpf355tqda",
                    record_id: 431,
                    latest_summary: SUMMARY,
                    all_record_reviews_count: 1,
                })),
            ),
        )
        await inspect()

        const cells = await recordCells()
        // Column order: record_type, record_public_ref, record_match_key, record_id.
        // Asserting positions, not just presence: a swap would leave both
        // strings on the page and satisfy any "is it rendered" check.
        expect(cells[0]).toBe("species")
        expect(cells[1]).toBe("spc_vu7cuk4s37szxaudjpf355tqda")
        expect(cells[2]).toBe("431")
        expect(cells[3]).toBe("431")
    })

    it("headings name what the columns hold", async () => {
        server.use(
            http.get(
                "/api/v1/admin/submissions/7/machine-review-inspection",
                () => HttpResponse.json(inspectionBody({
                    record_type: "species",
                    record_match_key: "431",
                    record_public_ref: "spc_vu7cuk4s37szxaudjpf355tqda",
                    record_id: 431,
                    latest_summary: SUMMARY,
                    all_record_reviews_count: 1,
                })),
            ),
        )
        await inspect()

        const table = (await screen.findAllByRole("table")).at(-1)!
        const headings = within(table)
            .getAllByRole("columnheader")
            .map((h) => h.textContent)

        expect(headings).toContain("record_public_ref")
        expect(headings).toContain("record_match_key")
        // The old name promised a public ref and delivered a row id.
        expect(headings).not.toContain("record_ref")
    })

    it("a record that cannot be named shows a dash, not its row id", async () => {
        /**
         * `applied_energy_correction` has no `public_ref` column, so the
         * backend answers `null`. The column must stay empty rather than fall
         * back to `record_id` -- a row id under a heading that says "public
         * ref" is exactly the defect this change removes.
         */
        server.use(
            http.get(
                "/api/v1/admin/submissions/7/machine-review-inspection",
                () => HttpResponse.json(inspectionBody({
                    record_type: "applied_energy_correction",
                    record_match_key: "88",
                    record_public_ref: null,
                    record_id: 88,
                    latest_summary: SUMMARY,
                    all_record_reviews_count: 1,
                })),
            ),
        )
        await inspect()

        const cells = await recordCells()
        expect(cells[1]).not.toBe("88")
        expect(cells[1].trim()).not.toBe("")
        expect(cells[2]).toBe("88")
        expect(cells[3]).toBe("88")
    })
})
