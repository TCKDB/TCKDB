import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { EntryThermoSection } from "./EntryThermoSection"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const entryRef = "spe_test_ch3"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/thermo`

function page() {
    return render(
        <MemoryRouter>
            <EntryThermoSection entryRef={entryRef} />
        </MemoryRouter>,
    )
}

/**
 * Deliberately NOT a copy of the live 3-records-all-identical-model-kind
 * fixture measured against spe_bcbdjwkip75yoziblpntwzblzu — a fixture that
 * faithfully mirrored production would make every mutation this file tests
 * for unobservable (lesson 2 in the brief: "a realistic fixture inherits
 * production's accidental regularities"). Three records that differ in
 * every field under test:
 *
 * - thm_alpha: nasa, computed, supersession null, nasa present / nasa9 null.
 * - thm_beta:  nasa9, estimated, supersession NON-null, nasa null / nasa9 present.
 * - thm_gamma: wilhoit, experimental, supersession null, all four model
 *   fields null except wilhoit — the "scalar-only" case.
 */
function mockRecords() {
    return [
        {
            thermo_ref: "thm_alpha",
            scientific_origin: "computed",
            model_kind: "nasa",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
            supersession: null,
            h298_kj_mol: 111.1,
            s298_j_mol_k: 222.2,
            h298_uncertainty_kj_mol: null,
            s298_uncertainty_j_mol_k: null,
            nasa: {
                t_low: 100, t_mid: 1000, t_high: 3000,
                low_temperature_coefficients: [1, 2, 3, 4, 5, 6, 7],
                high_temperature_coefficients: [8, 9, 10, 11, 12, 13, 14],
            },
            nasa9: null,
            wilhoit: null,
            points: null,
            temperature_coverage: {
                requested_min_k: null, requested_max_k: null,
                record_min_k: 100, record_max_k: 3000,
                covers_requested_range: true, overlap_fraction: null, extrapolation_distance_k: 0,
            },
            evidence_completeness: { score: 6, max: 8, checklist: { has_source_calculations: true, has_uncertainty: false } },
            provenance: {
                primary_calculation: { calculation_ref: "calc_alpha_sp", calculation_type: "sp", converged: null, geometry_validation_status: "not_present", scf_stability_status: "not_present", level_of_theory: null, software: null },
                level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", level_of_theory_ref: "lot_alpha" },
                software: null,
                statmech_ref: "sm_alpha",
                freq_calculation_ref: "calc_alpha_freq",
                sp_calculation_ref: "calc_alpha_sp",
            },
            group_additivity: null,
        },
        {
            thermo_ref: "thm_beta",
            scientific_origin: "estimated",
            model_kind: "nasa9",
            review: { status: "approved", reviewed_at: "2026-08-01T00:00:00", reviewer_kind: "human" },
            supersession: {
                superseded_by: "thm_beta_v2",
                current: "thm_beta_v3",
                reason: "corrected transcription error",
                superseded_at: "2026-08-15T00:00:00",
                chain_length: 2,
            },
            h298_kj_mol: 333.3,
            s298_j_mol_k: 444.4,
            h298_uncertainty_kj_mol: 1.5,
            s298_uncertainty_j_mol_k: 2.5,
            nasa: null,
            nasa9: [
                { interval_index: 0, t_min_k: 100, t_max_k: 1000, a1: 1, a2: 2, a3: 3, a4: 4, a5: 5, a6: 6, a7: 7, a8: 8, a9: 9 },
            ],
            wilhoit: null,
            points: null,
            temperature_coverage: null,
            evidence_completeness: { score: 3, max: 8, checklist: { has_source_calculations: false } },
            provenance: {
                primary_calculation: null,
                level_of_theory: null,
                software: null,
                statmech_ref: null,
                freq_calculation_ref: null,
                sp_calculation_ref: null,
            },
            group_additivity: {
                scheme_ref: "gas_1", scheme_name: "Benson v2", scheme_version: "2.0",
                code_commit: "abc123", note: null,
                components: [{ component_kind: "group", group_label: "C/H3", count: 1, h298_contribution_kj_mol: 10, s298_contribution_j_mol_k: 20, cp298_contribution_j_mol_k: null }],
            },
        },
        {
            thermo_ref: "thm_gamma",
            scientific_origin: "experimental",
            model_kind: "wilhoit",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
            supersession: null,
            h298_kj_mol: 555.5,
            s298_j_mol_k: 666.6,
            h298_uncertainty_kj_mol: null,
            s298_uncertainty_j_mol_k: null,
            nasa: null,
            nasa9: null,
            wilhoit: { cp0_j_mol_k: 33.3, cp_inf_j_mol_k: 99.9, b_k: 500, a0: 1, a1: 2, a2: 3, a3: 4, h0_kj_mol: null, s0_j_mol_k: null },
            points: null,
            temperature_coverage: null,
            evidence_completeness: { score: 2, max: 8, checklist: {} },
            provenance: { primary_calculation: null, level_of_theory: null, software: null, statmech_ref: null, freq_calculation_ref: null, sp_calculation_ref: null },
            group_additivity: null,
        },
    ]
}

function mockResponse(overrides: { records?: unknown[] } = {}) {
    const records = overrides.records ?? mockRecords()
    return {
        species_entry_ref: entryRef,
        review_summary: { approved: 1, under_review: 0, not_reviewed: 2, deprecated: 0, rejected: 0, total: 3 },
        records,
        pagination: { offset: 0, limit: 50, returned: records.length, total: records.length, post_collapse_total: records.length },
    }
}

describe("EntryThermoSection", () => {
    it("renders every deposited record independently — never merges, never picks one", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        expect(screen.getByText("thm_alpha")).toBeVisible()
        expect(screen.getByText("thm_beta")).toBeVisible()
        expect(screen.getByText("thm_gamma")).toBeVisible()
        expect(screen.getByText("3 records · review: 1 approved · 2 not reviewed")).toBeVisible()
    })

    it("scopes each record's own H298/S298 to its own card — never shows one record's value on another's row", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")

        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(within(alphaCard).getByText("111.1 kJ/mol")).toBeVisible()
        expect(within(alphaCard).queryByText("333.3 kJ/mol")).not.toBeInTheDocument()
        expect(within(alphaCard).queryByText("555.5 kJ/mol")).not.toBeInTheDocument()

        const betaCard = screen.getByText("thm_beta").closest("article") as HTMLElement
        expect(within(betaCard).getByText("333.3 kJ/mol")).toBeVisible()
        expect(within(betaCard).queryByText("111.1 kJ/mol")).not.toBeInTheDocument()

        const gammaCard = screen.getByText("thm_gamma").closest("article") as HTMLElement
        expect(within(gammaCard).getByText("555.5 kJ/mol")).toBeVisible()
        expect(within(gammaCard).queryByText("111.1 kJ/mol")).not.toBeInTheDocument()
        expect(within(gammaCard).queryByText("333.3 kJ/mol")).not.toBeInTheDocument()
    })

    it("distinguishes a null nasa9 (no NASA-9 polynomial recorded) from a populated one — never renders it as 'not requested'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")

        // thm_alpha has nasa9: null and nasa: populated.
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(within(alphaCard).getByText("No NASA-9 polynomial recorded for this record.")).toBeVisible()
        expect(within(alphaCard).queryByText(/not requested/i)).not.toBeInTheDocument()
        expect(within(alphaCard).getByRole("table", { name: /NASA-7 coefficients/ })).toBeVisible()

        // thm_beta is the mirror image: nasa: null, nasa9: populated.
        const betaCard = screen.getByText("thm_beta").closest("article") as HTMLElement
        expect(within(betaCard).getByText("No NASA-7 polynomial recorded for this record.")).toBeVisible()
        expect(within(betaCard).getByRole("table", { name: /NASA-9 intervals/ })).toBeVisible()

        // thm_gamma has neither nasa nor nasa9 (wilhoit only) — both must
        // read as absent facts, and wilhoit itself must be populated.
        const gammaCard = screen.getByText("thm_gamma").closest("article") as HTMLElement
        expect(within(gammaCard).getByText("No NASA-7 polynomial recorded for this record.")).toBeVisible()
        expect(within(gammaCard).getByText("No NASA-9 polynomial recorded for this record.")).toBeVisible()
        expect(within(gammaCard).queryByText("No Wilhoit fit recorded for this record.")).not.toBeInTheDocument()
    })

    it("never hides a superseded record — renders the notice alongside the record, not instead of it", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_beta")

        const betaCard = screen.getByText("thm_beta").closest("article") as HTMLElement
        expect(within(betaCard).getByText("Superseded")).toBeVisible()
        expect(within(betaCard).getByText(/thm_beta_v2/)).toBeVisible()
        expect(within(betaCard).getByText(/thm_beta_v3/)).toBeVisible()
        expect(within(betaCard).getByText(/corrected transcription error/)).toBeVisible()
        // The record's own data is still fully present, not replaced by the notice.
        expect(within(betaCard).getByText("333.3 kJ/mol")).toBeVisible()

        // The two non-superseded records carry no notice at all.
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(within(alphaCard).queryByText("Superseded")).not.toBeInTheDocument()
    })

    it("renders group-additivity provenance only for the record that has it", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_beta")
        const betaCard = screen.getByText("thm_beta").closest("article") as HTMLElement
        expect(within(betaCard).getByText("Benson v2 (2.0)")).toBeVisible()
        expect(within(betaCard).getByText("C/H3")).toBeVisible()

        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(within(alphaCard).queryByText(/Group-additivity estimation/)).not.toBeInTheDocument()
    })

    it("links calculation provenance refs to their calculation detail pages, and leaves the un-paged statmech ref as text", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        // calc_alpha_sp is deliberately both the primary calculation and the
        // sp calculation on this fixture record — two separate links, same
        // target ref, each still its own correctly-hrefed anchor.
        const spLinks = within(alphaCard).getAllByRole("link", { name: "calc_alpha_sp" })
        expect(spLinks).toHaveLength(2)
        for (const link of spLinks) expect(link).toHaveAttribute("href", "/calculations/calc_alpha_sp")
        expect(within(alphaCard).getByRole("link", { name: "calc_alpha_freq" })).toHaveAttribute("href", "/calculations/calc_alpha_freq")
        expect(within(alphaCard).getByText("sm_alpha")).toBeVisible()
        expect(within(alphaCard).queryByRole("link", { name: "sm_alpha" })).not.toBeInTheDocument()
    })

    it("states honestly when no thermo records are deposited for this entry", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [] }))))
        page()
        expect(await screen.findByText("No thermochemistry records are deposited for this entry.")).toBeVisible()
    })

    it("distinguishes loading from a 404 archive response", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ detail: "not found" }, { status: 404 })))
        page()
        expect(screen.getByRole("heading", { name: "Loading thermochemistry…" })).toBeVisible()
        expect(await screen.findByRole("heading", { name: "Thermochemistry not found" })).toBeVisible()
    })
})
