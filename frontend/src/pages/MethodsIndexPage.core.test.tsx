import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import MethodsIndexPage from "./MethodsIndexPage"

// The core-treatment filter is the API's own `core_treatment` parameter: choosing a value must
// re-query the server, not only narrow what is already on the page.

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const LOT_ENDPOINT = "/api/v1/scientific/level-of-theories/browse"

function row(ref: string, core: string | null, spin: string | null = null) {
    return {
        level_of_theory: {
            level_of_theory_ref: ref, method: "CCSD(T)", basis: "cc-pCVTZ", dispersion: null, solvent: null,
            label: core ? `CCSD(T)/cc-pCVTZ (core=${core})` : "CCSD(T)/cc-pCVTZ",
            core_treatment: core, spin_treatment: spin, lot_hash: `h_${ref}`, created_at: "2026-10-03T00:00:00Z",
        },
        evidence_summary: { calculation_usage_count: 2, has_correction_schemes: false, has_frequency_scale_factors: false, distinct_software_count: 1 },
        available_sections: { has_correction_schemes: false, has_frequency_scale_factors: false, has_used_by: false, has_software: false },
    }
}

const ALL = [row("lot_fc", "frozen_core"), row("lot_ae", "all_electron"), row("lot_none", null, "restricted")]

function serve(requests: string[]) {
    server.use(
        http.get(LOT_ENDPOINT, ({ request }) => {
            const url = new URL(request.url)
            requests.push(url.search)
            const core = url.searchParams.get("core_treatment")
            const records = core ? ALL.filter((r) => r.level_of_theory.core_treatment === core) : ALL
            return HttpResponse.json({ records, pagination: { offset: 0, limit: 200, returned: records.length, total: records.length } })
        }),
        http.get("/api/v1/scientific/meta/software", () => HttpResponse.json({ results: [] })),
        http.get("/api/v1/scientific/meta/workflow-tools", () => HttpResponse.json({ results: [] })),
    )
}

function page() {
    return render(<MemoryRouter><MethodsIndexPage /></MemoryRouter>)
}

describe("MethodsIndexPage core-treatment filter", () => {
    it("re-queries the API with core_treatment when the choice changes, and again when it is cleared", async () => {
        const requests: string[] = []
        serve(requests)
        const user = userEvent.setup()
        page()
        const table = await screen.findByRole("table", { name: "Levels of theory" })
        expect(within(table).getAllByRole("row")).toHaveLength(4)
        expect(requests).toHaveLength(1)
        expect(requests[0]).not.toContain("core_treatment")

        await user.selectOptions(screen.getByLabelText("Core treatment"), "frozen_core")

        await waitFor(() => expect(requests).toHaveLength(2))
        expect(requests[1]).toContain("core_treatment=frozen_core")
        await waitFor(() => expect(within(screen.getByRole("table", { name: "Levels of theory" })).getAllByRole("row")).toHaveLength(2))

        await user.selectOptions(screen.getByLabelText("Core treatment"), "")
        await waitFor(() => expect(requests).toHaveLength(3))
        expect(requests[2]).not.toContain("core_treatment")
    })

    it("keeps the control when a choice matches nothing, with the filter's own empty state", async () => {
        const requests: string[] = []
        server.use(
            http.get(LOT_ENDPOINT, ({ request }) => {
                requests.push(new URL(request.url).search)
                const records = new URL(request.url).searchParams.get("core_treatment") ? [] : [row("lot_none", null)]
                return HttpResponse.json({ records, pagination: { offset: 0, limit: 200, returned: records.length, total: records.length } })
            }),
            http.get("/api/v1/scientific/meta/software", () => HttpResponse.json({ results: [] })),
            http.get("/api/v1/scientific/meta/workflow-tools", () => HttpResponse.json({ results: [] })),
        )
        const user = userEvent.setup()
        page()
        await screen.findByRole("table", { name: "Levels of theory" })
        await user.selectOptions(screen.getByLabelText("Core treatment"), "all_electron")
        expect(await screen.findByText("No levels of theory match this filter.")).toBeVisible()
        expect(screen.getByLabelText("Core treatment")).toBeVisible()
    })

    it("shows core and spin treatment columns, 'not recorded' where a level does not say", async () => {
        serve([])
        page()
        const table = await screen.findByRole("table", { name: "Levels of theory" })
        const cells = (label: string) => Array.from(table.querySelectorAll(`td[data-label="${label}"]`)).map((c) => c.textContent)
        expect(cells("Core treatment")).toEqual(["frozen core", "all electron", "not recorded"])
        expect(cells("Spin treatment")).toEqual(["not recorded", "not recorded", "restricted"])
    })

    it("prints the server's full label, so all-electron and frozen-core levels do not read alike", async () => {
        serve([])
        page()
        const table = await screen.findByRole("table", { name: "Levels of theory" })
        const links = within(table).getAllByRole("link").map((a) => a.textContent)
        expect(links).toEqual([
            "CCSD(T)/cc-pCVTZ (core=frozen_core)",
            "CCSD(T)/cc-pCVTZ (core=all_electron)",
            "CCSD(T)/cc-pCVTZ",
        ])
    })

    it("puts the key columns before dispersion and solvent", async () => {
        serve([])
        page()
        const table = await screen.findByRole("table", { name: "Levels of theory" })
        expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
            "Level of theory", "Core treatment", "Spin treatment", "Calculations", "Dispersion", "Solvent",
        ])
    })
})
