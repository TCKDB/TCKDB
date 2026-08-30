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
                software_release: null,
                workflow_tool_release: null,
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
                software_release: null,
                workflow_tool_release: null,
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
            provenance: { primary_calculation: null, level_of_theory: null, software_release: null, workflow_tool_release: null, statmech_ref: null, freq_calculation_ref: null, sp_calculation_ref: null },
            group_additivity: null,
        },
    ]
}

function mockResponse(overrides: { records?: unknown[]; pagination?: Record<string, number> } = {}) {
    const records = overrides.records ?? mockRecords()
    return {
        species_entry_ref: entryRef,
        review_summary: { approved: 1, under_review: 0, not_reviewed: 2, deprecated: 0, rejected: 0, total: 3 },
        records,
        pagination: overrides.pagination
            ?? { offset: 0, limit: 50, returned: records.length, total: records.length, post_collapse_total: records.length },
    }
}

/**
 * Binds a `<dt>` label to its own `<dd>` value by DOM adjacency, not by
 * "is this text present anywhere on the card" — a query that only checks
 * presence cannot tell a correctly-labelled row from a swapped one (both
 * put the same two strings somewhere in the same card). Direct DOM
 * traversal rather than `getByRole("term", ...)`: `<dt>`/`<dd>` do carry
 * the implicit `term`/`definition` roles here (verified against this
 * project's aria-query version), but the accessible-name computation for
 * those roles does not pick up plain text-node content, so a role query
 * with `{ name }` finds the elements with an empty computed name and never
 * matches — confirmed with a throwaway sandbox test before writing this.
 */
function ddFor(container: HTMLElement, term: string): string {
    const dt = Array.from(container.querySelectorAll("dt")).find((el) => el.textContent === term)
    if (!dt) throw new Error(`No <dt> with text "${term}" found in this container`)
    return dt.nextElementSibling?.textContent ?? ""
}

/**
 * Finds the `<code>` element whose immediately preceding text node
 * contains `precedingText`, and returns its own text. Used to bind a
 * supersession pointer to ITS OWN sentence rather than merely asserting
 * both refs appear somewhere in the notice — `getByText(/thm_beta_v2/)`
 * matches identically whether `superseded_by`/`current` are rendered in
 * the correct order or swapped, since both refs are present either way.
 * A plain-string/regex `getByText` also cannot span the `<p>`/`<code>`
 * boundary at all here (testing-library's default text matcher reads only
 * an element's own direct text-node children, not descendant elements —
 * see the `CalculationDetailPage.test.tsx` caveat-sentence comment for the
 * same limitation), so this reads the DOM directly instead.
 */
function codeAfter(container: HTMLElement, precedingText: string): string {
    const codes = Array.from(container.querySelectorAll("code"))
    for (const code of codes) {
        if ((code.previousSibling?.textContent ?? "").includes(precedingText)) return code.textContent ?? ""
    }
    throw new Error(`No <code> immediately preceded by text containing "${precedingText}" found`)
}

/**
 * Reads the text of the `<td data-label="...">` cell within `row` matching
 * `label` — column-scoped, not row-scoped. `within(row).getByText(value)`
 * only proves a value is SOMEWHERE in the row; it cannot tell a value
 * sitting under its correct column from the same value sitting under a
 * swapped one (a1/a2 transposed, T min/T max inverted, a Top/Invalidated
 * column swap). Every table this file renders tags each `<td>` with its own
 * `data-label`, so this binds a value to the specific column it claims to
 * be in.
 */
function cellAt(row: HTMLElement, label: string): string {
    const cell = row.querySelector(`td[data-label="${label}"]`)
    if (!cell) throw new Error(`No <td data-label="${label}"> found in this row`)
    return cell.textContent ?? ""
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

    it("binds each record's own H298 and S298 to their own labelled row — never a swapped label, never another record's value", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")

        // Values are formatted through `domain/quantityFormat.ts`'s
        // `formatQuantity("thermo_h298_kj_mol"/"thermo_s298_j_mol_k", ...)`
        // now, at the 2dp precision ported from `landing.py`'s digits table
        // -- not the raw double the old local `formatQuantity` printed. See
        // `quantityFormat.test.ts` for the rounding rule itself; this test
        // stays about *binding*, so it only needs values that expose a
        // swap, which "111.10" vs "222.20" etc. still do.
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(ddFor(alphaCard, "H298")).toBe("111.10 kJ/mol")
        expect(ddFor(alphaCard, "S298")).toBe("222.20 J/mol·K")

        const betaCard = screen.getByText("thm_beta").closest("article") as HTMLElement
        expect(ddFor(betaCard, "H298")).toBe("333.30 kJ/mol")
        expect(ddFor(betaCard, "S298")).toBe("444.40 J/mol·K")

        const gammaCard = screen.getByText("thm_gamma").closest("article") as HTMLElement
        expect(ddFor(gammaCard, "H298")).toBe("555.50 kJ/mol")
        expect(ddFor(gammaCard, "S298")).toBe("666.60 J/mol·K")
    })

    it("renders the NASA-7 low- and high-temperature coefficient rows in their own row, unswapped", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        const table = within(alphaCard).getByRole("table", { name: /NASA-7 coefficients/ })
        const rows = within(table).getAllByRole("row").slice(1) // drop header row
        const lowRow = rows.find((row) => within(row).queryByText("Low"))!
        const highRow = rows.find((row) => within(row).queryByText("High"))!
        const lowCells = within(lowRow).getAllByRole("cell").slice(1).map((cell) => cell.textContent)
        const highCells = within(highRow).getAllByRole("cell").slice(1).map((cell) => cell.textContent)
        expect(lowCells).toEqual(["1", "2", "3", "4", "5", "6", "7"])
        expect(highCells).toEqual(["8", "9", "10", "11", "12", "13", "14"])
    })

    it("binds NASA-7's T-low and T-high scalars to their own labelled row — never swapped", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        const alphaCard = (await screen.findByText("thm_alpha")).closest("article") as HTMLElement
        // thm_alpha's fixture: t_low: 100, t_mid: 1000, t_high: 3000 — three
        // distinct values, so a T-low/T-high swap is observable.
        expect(ddFor(alphaCard, "T low (K)")).toBe("100")
        expect(ddFor(alphaCard, "T mid (K)")).toBe("1000")
        expect(ddFor(alphaCard, "T high (K)")).toBe("3000")
    })

    it("binds Wilhoit's Cp0 and Cp-infinity to their own labelled row — never swapped", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        const gammaCard = (await screen.findByText("thm_gamma")).closest("article") as HTMLElement
        // thm_gamma's fixture: cp0_j_mol_k: 33.3, cp_inf_j_mol_k: 99.9.
        expect(ddFor(gammaCard, "Cp0 (J/mol·K)")).toBe("33.3")
        expect(ddFor(gammaCard, "Cp∞ (J/mol·K)")).toBe("99.9")
    })

    it("renders each NASA-9 interval's own row exactly, with a1..a9 in their own columns — never transposed, never with T min/T max inverted", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        const betaCard = (await screen.findByText("thm_beta")).closest("article") as HTMLElement
        const table = within(betaCard).getByRole("table", { name: /NASA-9 intervals/ })
        const row = within(table).getAllByRole("row")[1] // header row, then one interval row
        // thm_beta's fixture: interval_index 0, t_min_k 100, t_max_k 1000,
        // a1..a9 = 1..9 — twelve distinct values, so any column transposition
        // (a1<->a2, T min<->T max, or any other pair) is observable.
        expect(cellAt(row, "Interval")).toBe("0")
        expect(cellAt(row, "T min (K)")).toBe("100")
        expect(cellAt(row, "T max (K)")).toBe("1000")
        for (let index = 1; index <= 9; index += 1) {
            expect(cellAt(row, `a${index}`)).toBe(String(index))
        }
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
        // Position-bound, not "both refs appear somewhere": superseded_by
        // must be the ref in the "replaced by" sentence and current must be
        // the ref in the "current record in this chain is" sentence — a
        // direction swap (superseded_by <-> current) renders identically
        // under a presence-only check, since both refs are on the card
        // either way. See `codeAfter`'s docstring.
        expect(codeAfter(betaCard, "replaced by")).toBe("thm_beta_v2")
        expect(codeAfter(betaCard, "current record in this chain is")).toBe("thm_beta_v3")
        expect(within(betaCard).getByText(/corrected transcription error/)).toBeVisible()
        // The record's own data is still fully present, not replaced by the
        // notice. `getByText` only reads an element's own direct text-node
        // children (see `ddFor`'s docstring above), and the unit now
        // renders in its own `<span>` (so it can be styled/read
        // independently, per the digits-table rule that "the unit belongs
        // on screen in its own element") -- so this reads the H298 row the
        // same way the binding test above does, rather than a plain
        // `getByText` that would never match a value split across elements.
        expect(ddFor(betaCard, "H298")).toBe("333.30 kJ/mol")

        // The two non-superseded records carry no notice at all.
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(within(alphaCard).queryByText("Superseded")).not.toBeInTheDocument()
    })

    it("renders group-additivity provenance data only for the record that has it, but the section itself (with an explicit absence line) on every record", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_beta")
        const betaCard = screen.getByText("thm_beta").closest("article") as HTMLElement
        expect(within(betaCard).getByText("Benson v2 (2.0)")).toBeVisible()
        expect(within(betaCard).getByText("C/H3")).toBeVisible()

        // thm_alpha has group_additivity: null — an absent scientific fact,
        // consistent with nasa/nasa9/wilhoit/points: the section heading
        // still renders, with an explicit "not recorded" line rather than
        // being silently omitted.
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(within(alphaCard).getByText("Group-additivity estimation")).toBeVisible()
        expect(within(alphaCard).getByText("No group-additivity estimation recorded for this record.")).toBeVisible()
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

    it("renders the thermo's OWN software/workflow-tool provenance when populated — never 'not recorded' for a served value (issue #284)", async () => {
        // Every other fixture in this file carries `software_release: null` /
        // `workflow_tool_release: null`, which would render "not recorded"
        // whether the API served the keys correctly or a regression dropped
        // them entirely — a null-only fixture cannot distinguish "served" from
        // "dropped". This is the one populated case.
        const [alpha, ...rest] = mockRecords()
        const populated = {
            ...alpha,
            thermo_ref: "thm_delta",
            provenance: {
                ...alpha.provenance,
                software_release: { software_release_ref: "srel_arkane", software: "Arkane", version: "1.0" },
                workflow_tool_release: { workflow_tool_release_ref: "wfr_arc", workflow_tool: "ARC", version: "1.1.0" },
            },
        }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [populated, ...rest] }))))
        page()
        await screen.findByText("thm_delta")
        const deltaCard = screen.getByText("thm_delta").closest("article") as HTMLElement
        expect(ddFor(deltaCard, "Software")).toBe("Arkane 1.0")
        expect(ddFor(deltaCard, "Workflow tool")).toBe("ARC 1.1.0")
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

    it("shows the '(showing M)' honesty note only when returned is short of total, with the real deposit total as the headline count", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({
            records: mockRecords().slice(0, 2),
            pagination: { offset: 0, limit: 2, returned: 2, total: 5, post_collapse_total: 5 },
        }))))
        page()
        // "5 records" is the deposit total (`pagination.total`), never
        // `pagination.returned` (2) printed in its place — that swap would
        // silently delete this note's entire reason for existing.
        expect(await screen.findByText("5 records (showing 2) · review: 1 approved · 2 not reviewed")).toBeVisible()
        expect(screen.queryByText(/^2 records/)).not.toBeInTheDocument()
    })
})
