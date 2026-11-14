import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import CorrectionSchemePage from "./CorrectionSchemePage"
import { ADVERSARIAL, expectNoAdversarialText, expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
import { titleHasKind } from "../test/routeTitleKinds"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

function page(ref = "ecs_bac") {
    return render(
        <MemoryRouter initialEntries={[`/methods/schemes/${ref}`]}>
            <Routes>
                <Route path="/methods/schemes/:ecsRef" element={<CorrectionSchemePage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function mockResponse(overrides: Record<string, unknown> = {}) {
    return {
        request: { include: [] },
        review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 },
        record: {
            energy_correction_scheme: {
                energy_correction_scheme_ref: "ecs_bac",
                name: "bac_petersson",
                scheme_kind: "bac_petersson",
                version: null,
                units: "kcal_mol",
                note: "Per-species BAC computed by Arkane (bac_type=p).",
                created_at: "2026-07-21T11:59:29Z",
            },
            level_of_theory: {
                level_of_theory_ref: "lot_b3lyp",
                method: "b3lyp",
                basis: "def2tzvp",
                dispersion: null,
                solvent: null,
                spin_treatment: null,
                label: null,
                display: "b3lyp/def2tzvp",
            },
            // Software named without a release ref; no workflow-tool release.
            software_release: { software_release_ref: "", software: "Gaussian", version: null },
            workflow_tool_release: null,
            literature: null,
            evidence_summary: { atom_param_count: 0, bond_param_count: 2, component_param_count: 0, has_corrections: true, applied_usage_count: 82, has_applied_usage: true, has_literature_source: false },
            available_sections: { has_corrections: true, has_used_by: true, has_literature: false },
            corrections: [
                { correction_kind: "bond", target: "C-H", value: 1.1, component_kind: null },
                { correction_kind: "bond", target: "C-C", value: 2.2, component_kind: null },
            ],
            used_by: [{
                record_type: "species_entry", record_ref: "spe_one", record_id: null, endpoint: "/api/v1/scientific/species-entries/spe_one",
                application_role: "bac_total", applied_value: -3.6, applied_value_unit: "kcal_mol", applied_value_hartree: -0.0057,
                temperature_k: null, applied_note: null, source_calculation_ref: "calc_one", source_calculation_endpoint: "/api/v1/scientific/calculations/calc_one",
                component_count: 2,
            }],
            ...overrides,
        },
    }
}

describe("CorrectionSchemePage: the standalone scheme page", () => {
    it("renders the scheme identity, its parameter table, its owning level of theory (linked), and its applications", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse())))
        page()
        // Titled from the controlled `scheme_kind` vocabulary, not the depositor's own `name` text.
        expect(await screen.findByRole("heading", { name: "Petersson bond-additivity correction, Gaussian", level: 1 })).toBeVisible()
        await waitFor(() => expect(titleHasKind(document.title, "/methods/schemes/:ref")).toBe(true))
        // The level of theory is linked in the breadcrumb and once in the identity facts.
        const lotLinks = screen.getAllByRole("link", { name: "b3lyp/def2tzvp" })
        expect(lotLinks).toHaveLength(2)
        for (const link of lotLinks) expect(link).toHaveAttribute("href", "/methods/lot_b3lyp")

        const paramTable = screen.getByRole("table", { name: "Bond correction parameters" })
        expect(within(paramTable).getByText("C-H")).toBeVisible()
        expect(within(paramTable).getByText("C-C")).toBeVisible()

        const appTable = screen.getByRole("table", { name: "Applications of this correction scheme" })
        expect(within(appTable).getByText("spe_one")).toBeVisible()
        const calcLink = within(appTable).getByRole("link", { name: "calc_one" })
        expect(calcLink).toHaveAttribute("href", "/calculations/calc_one")
    })

    it("reads a null level_of_theory honestly, as 'not tied to a specific level of theory' -- never 'any level of theory' or blank", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({ level_of_theory: null }))))
        page()
        // No level-of-theory suffix at all when there is none to link.
        await screen.findByRole("heading", { name: "Petersson bond-additivity correction, Gaussian", level: 1 })
        expect(screen.getByText("not tied to a specific level of theory")).toBeVisible()
    })

    /**
     * Mutation check: titles this page from `scheme.scheme_kind` via the
     * controlled `schemeKindLabel` vocabulary, never from the depositor's
     * own free-text `energy_correction_scheme.name`. The `name` here is deliberately something a controlled-vocabulary
     * label could never produce, so a regression that reverts the heading
     * to `scheme.name` is unambiguous: this test would then find the
     * literal depositor string on the page instead of the archive's own
     * label, and the `getByRole("heading", ...)` query below would find no
     * match at all (fail, not silently pass).
     */
    it("titles the page from the controlled scheme_kind vocabulary, never the depositor's free-text name", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({
            energy_correction_scheme: { ...mockResponse().record.energy_correction_scheme, name: "Bob's custom BAC v3 (DO NOT USE)", scheme_kind: "atom_energy" },
        }))))
        page()
        expect(await screen.findByRole("heading", { name: "Atom-energy correction, Gaussian", level: 1 })).toBeVisible()
        expect(screen.queryByText("Bob's custom BAC v3 (DO NOT USE)")).not.toBeInTheDocument()
    })

    it("renders a deposited literature source", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({
            literature: { literature_ref: "lit_one", title: "A BAC parameterisation study", title_source: "resolved_metadata", year: 2019, doi: null },
        }))))
        page()
        await screen.findByRole("heading", { level: 1 })
        expect(screen.getByText("Literature source")).toBeVisible()
        expect(screen.getByText("A BAC parameterisation study (2019)")).toBeVisible()
    })

    it("withholds a depositor-typed literature title and DOI, keeping the year after the ref (guard)", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({
            literature: {
                literature_ref: "lit_one", title: "DEPOSITOR_SENTINEL_literature_title", title_source: "depositor_supplied",
                year: 2019, doi: "10.1/ DEPOSITOR_SENTINEL_literature_doi spam.example",
            },
        }))))
        page()
        await screen.findByRole("heading", { level: 1 })
        expect(screen.getByText("Literature source")).toBeVisible()
        expect(screen.getByText("Literature source").nextElementSibling?.textContent).toBe("lit_one (2019) · Title not verified")
        expect(document.body.innerHTML).not.toContain("DEPOSITOR_SENTINEL")
    })

    /** An absent literature source reads as its own "not recorded" row, never as an omitted row. */
    it("states a missing literature source plainly, as its own row, rather than omitting it", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({ literature: null }))))
        page()
        await screen.findByRole("heading", { level: 1 })
        const dt = screen.getByText("Literature source")
        expect(dt).toBeVisible()
        expect(dt.nextElementSibling).toHaveTextContent("not recorded")
    })

    /** Software is part of the title and is never a link; the workflow-tool release is its own row. */
    it("titles the scheme with its software and renders the workflow-tool release as its own row", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({
            workflow_tool_release: { workflow_tool_release_ref: "wfr_one", workflow_tool: "ARC", version: "1.1.0" },
        }))))
        page()
        await screen.findByRole("heading", { level: 1 })
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Petersson bond-additivity correction, Gaussian")
        expect(screen.queryByText("Software")).not.toBeInTheDocument()
        // Never a link -- `software_release_ref` is empty on this row.
        expect(screen.queryByRole("link", { name: /Gaussian/ })).not.toBeInTheDocument()
        const toolDt = screen.getByText("Workflow-tool release")
        expect(toolDt.nextElementSibling).toHaveTextContent("ARC 1.1.0")
    })

    /**
     * `software_release_ref` is a real release ref, but `GET /api/v1/software-releases/{id}` sits behind
     * `require_auth_for_legacy_reads` and 401s anonymously, so this page
     * NEVER turns it into a link. It still earns its own row, as
     * copyable text.
     */
    it("shows the applied value with a spelled-out unit and links a species entry but not a statmech record", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({
            used_by: [
                {
                    record_type: "species_entry", record_ref: "spe_one", record_id: null, endpoint: "/x",
                    application_role: "bac_total", applied_value: -3.6, applied_value_unit: "kcal_mol", applied_value_hartree: -0.0057,
                    temperature_k: null, applied_note: null, source_calculation_ref: null, source_calculation_endpoint: null, component_count: 2,
                },
                {
                    record_type: "statmech", record_ref: "sm_one", record_id: null, endpoint: "/y",
                    application_role: "bac_total", applied_value: 1.5, applied_value_unit: "kj_mol", applied_value_hartree: 0.0005,
                    temperature_k: null, applied_note: null, source_calculation_ref: null, source_calculation_endpoint: null, component_count: 2,
                },
            ],
        }))))
        page()
        await screen.findByRole("heading", { level: 1 })
        const table = screen.getByRole("table", { name: "Applications of this correction scheme" })
        expect(within(table).getByRole("link", { name: "spe_one" })).toHaveAttribute("href", "/species-entries/spe_one")
        expect(within(table).queryByRole("link", { name: "sm_one" })).not.toBeInTheDocument()
        expect(within(table).getByText("sm_one")).toBeVisible()
        expect(table).toHaveTextContent("-3.6 kcal/mol")
        expect(table).toHaveTextContent("1.5 kJ/mol")
        expect(table).not.toHaveTextContent("kcal_mol")
    })

    it("puts the level of theory in the breadcrumb and the document title", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByRole("heading", { level: 1 })
        const crumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(crumb).getByRole("link", { name: "b3lyp/def2tzvp" })).toHaveAttribute("href", "/methods/lot_b3lyp")
        await waitFor(() => expect(document.title).toContain("b3lyp/def2tzvp"))
    })

    it("renders a real software_release_ref as its own copyable row, never a link", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({
            software_release: { software_release_ref: "swr_g16", software: "Gaussian", version: "16" },
        }))))
        page()
        await screen.findByRole("heading", { level: 1 })
        const refDt = screen.getByText("Software release ref")
        expect(refDt.nextElementSibling).toHaveTextContent("swr_g16")
        expect(screen.queryByRole("link", { name: /swr_g16/ })).not.toBeInTheDocument()
    })

    it("renders no 'Software release ref' row when the release ref is empty", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByRole("heading", { level: 1 })
        expect(screen.queryByText("Software release ref")).not.toBeInTheDocument()
    })

    /**
     * Mutation check: omit an absent field entirely instead of
     * stating it. Workflow-tool release can be null when the archive could
     * not derive one unambiguously; citation can be null for an unrelated
     * reason (nobody recorded one). These are two DIFFERENT
     * absences and the page must not conflate them into one row or one
     * sentence -- so each keeps its OWN `<dt>`/`<dd>` pair, and neither
     * one's copy claims to know why the value is missing (never "not
     * applicable" -- a claim about chemistry this archive cannot make).
     */
    it("states a missing workflow-tool release plainly, as its own row distinct from the citation row, without narrating a cause", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({
            workflow_tool_release: null,
            literature: null,
        }))))
        page()
        await screen.findByRole("heading", { level: 1 })
        const toolDt = screen.getByText("Workflow-tool release")
        expect(toolDt).toBeVisible()
        expect(toolDt.nextElementSibling).toHaveTextContent("not recorded")
        const litDt = screen.getByText("Literature source")
        expect(litDt).toBeVisible()
        expect(litDt.nextElementSibling).toHaveTextContent("not recorded")
        // Two separate rows, not one merged statement about both.
        expect(toolDt).not.toBe(litDt)
        expect(screen.queryByText(/not applicable/i)).not.toBeInTheDocument()
    })
})

describe("CorrectionSchemePage -- no depositor-typed text", () => {
    it("prints none of the fields a depositor typed, in text or in any attribute", async () => {
        const planted = plantSentinels(mockResponse(), ["name", "note", "applied_note", "target"])
        expectPlanted(planted)
        const { value } = planted
        // Keys shaped like element/bond keys but not real elements.
        ;(value.record.corrections as { target: string }[]).forEach((row, index) => { row.target = index === 0 ? ADVERSARIAL.elementKey : "Xx" })
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(value)))
        page()
        expect(await screen.findByRole("heading", { name: "Petersson bond-additivity correction, Gaussian", level: 1 })).toBeVisible()
        expect(within(screen.getByRole("table", { name: "Applications of this correction scheme" })).getByText("spe_one")).toBeVisible()

        expectNoDepositorText()
        expectNoAdversarialText()
    })
})
