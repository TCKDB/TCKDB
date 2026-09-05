import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { EntryThermoSection } from "./EntryThermoSection"
import { PageSectionsProvider } from "./PageSections"
import { TableOfContents } from "./TableOfContents"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const entryRef = "spe_test_ch3"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/thermo`

/** Same as `page()` below, but with the real `TableOfContents` mounted
 *  alongside -- for the tests that assert what actually shows up in the
 *  page's jump list, not just what renders in the body. */
function pageWithToc(conformer?: ConformerProjection, conformers?: ConformerProjection[]) {
    return render(
        <MemoryRouter>
            <PageSectionsProvider>
                <TableOfContents />
                <EntryThermoSection entryRef={entryRef} conformer={conformer} conformers={conformers} />
            </PageSectionsProvider>
        </MemoryRouter>,
    )
}

function page(conformer?: ConformerProjection, conformers?: ConformerProjection[]) {
    return render(
        <MemoryRouter>
            <EntryThermoSection entryRef={entryRef} conformer={conformer} conformers={conformers} />
        </MemoryRouter>,
    )
}

function conformerGroup(ref: string, label: string): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: ref, label },
        observations_summary: { total: 1 },
        evidence_summary: {
            calculation_count: 1, optimization_chain_count: 1, geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 1, sp: 1 }, levels_of_theory: {},
        },
        observations: [], calculations: [], geometries: [],
    } as unknown as ConformerProjection
}
const conformerGroups = [conformerGroup("cg_one", "conformer_1"), conformerGroup("cg_two", "conformer_2")]

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
                // Population A: a real Gaussian chain on the CALCULATION, but
                // no software recorded on the thermo itself (software_release
                // below stays null). The one combination that catches a
                // reintroduced #284 -- a fallback from software_release to
                // primary_calculation.software would leak "Gaussian" here.
                primary_calculation: { calculation_ref: "calc_alpha_sp", calculation_type: "sp", converged: null, geometry_validation_status: "not_present", scf_stability_status: "not_present", level_of_theory: null, software: { software_release_ref: "srel_gaussian", software: "Gaussian", version: "16, Revision C.02" } },
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

    // Finding 16 (block review): a card used to render ALL FOUR model
    // blocks (NASA-7/NASA-9/Wilhoit/points) regardless of the record's own
    // `model_kind`, so a `nasa` record also carried an empty "NASA-9
    // polynomial" box, an empty "Wilhoit form" box, and an empty "Evaluated
    // points" box beneath its real NASA-7 table -- on every card. Only the
    // ONE block matching the record's own declared `model_kind` renders
    // now; the other three model-kind headings are absent entirely, not
    // present-with-an-absence-line.
    it("renders only the ONE model block matching the record's own model_kind — never the other three as empty boxes", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")

        // thm_alpha: model_kind nasa, nasa populated. NASA-7 renders; the
        // other three model-kind headings (and their absence text) do not
        // exist on this card at all.
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(within(alphaCard).getByRole("table", { name: /NASA-7 coefficients/ })).toBeVisible()
        expect(within(alphaCard).queryByText("NASA-9 polynomial")).not.toBeInTheDocument()
        expect(within(alphaCard).queryByText("Wilhoit form")).not.toBeInTheDocument()
        expect(within(alphaCard).queryByText("Evaluated points")).not.toBeInTheDocument()
        expect(within(alphaCard).queryByText(/not requested/i)).not.toBeInTheDocument()

        // thm_beta: model_kind nasa9, nasa9 populated. Only NASA-9 renders
        // -- NASA-7's own heading (and its "not recorded" line) is absent,
        // not present-and-empty.
        const betaCard = screen.getByText("thm_beta").closest("article") as HTMLElement
        expect(within(betaCard).getByRole("table", { name: /NASA-9 intervals/ })).toBeVisible()
        expect(within(betaCard).queryByText("NASA-7 polynomial")).not.toBeInTheDocument()
        expect(within(betaCard).queryByText("No NASA-7 polynomial recorded for this record.")).not.toBeInTheDocument()

        // thm_gamma: model_kind wilhoit, wilhoit populated. Only Wilhoit
        // renders -- neither NASA-7 nor NASA-9's heading exists on this
        // card.
        const gammaCard = screen.getByText("thm_gamma").closest("article") as HTMLElement
        expect(within(gammaCard).getByText("Cp0 (J/mol·K)")).toBeVisible()
        expect(within(gammaCard).queryByText("NASA-7 polynomial")).not.toBeInTheDocument()
        expect(within(gammaCard).queryByText("NASA-9 polynomial")).not.toBeInTheDocument()
    })

    // A `model_kind` claiming data it doesn't have is still the record's
    // OWN declared kind -- `ModelBlock` still renders that one block
    // defensively (its own "No X recorded" line), it just never falls
    // back to rendering a DIFFERENT kind's block instead.
    it("still renders the record's own declared model_kind's block defensively when its data is null — never falls back to a different kind's block", async () => {
        const [alpha] = mockRecords()
        const claimsNasaButEmpty = { ...alpha, thermo_ref: "thm_empty_nasa", nasa: null }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [claimsNasaButEmpty] }))))
        page()
        const card = (await screen.findByText("thm_empty_nasa")).closest("article") as HTMLElement
        expect(within(card).getByText("No NASA-7 polynomial recorded for this record.")).toBeVisible()
        expect(within(card).queryByText("NASA-9 polynomial")).not.toBeInTheDocument()
        expect(within(card).queryByText("Wilhoit form")).not.toBeInTheDocument()
        expect(within(card).queryByText("Evaluated points")).not.toBeInTheDocument()
    })

    // Finding 16: "REQUESTED RANGE (K): No temperature filter applied" /
    // "COVERS REQUESTED RANGE: Yes" / "EXTRAPOLATION DISTANCE (K): 0"
    // describe the REQUEST, not the record -- this page never sends a
    // temperature filter, so these three rows must not print on a record
    // whose `temperature_coverage.requested_min_k`/`requested_max_k` are
    // both null. `Record range (K)` is a fact of the record and stays.
    it("omits the request-scoped temperature-coverage rows when no range was requested, but keeps the record's own range", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        // thm_alpha's fixture: requested_min_k/requested_max_k both null,
        // record_min_k: 100, record_max_k: 3000.
        const alphaCard = (await screen.findByText("thm_alpha")).closest("article") as HTMLElement
        expect(ddFor(alphaCard, "Record range (K)")).toBe("100–3000")
        expect(within(alphaCard).queryByText("Requested range (K)")).not.toBeInTheDocument()
        expect(within(alphaCard).queryByText("Covers requested range")).not.toBeInTheDocument()
        expect(within(alphaCard).queryByText("Extrapolation distance (K)")).not.toBeInTheDocument()
        expect(within(alphaCard).queryByText("No temperature filter applied")).not.toBeInTheDocument()
    })

    it("shows the request-scoped temperature-coverage rows when a range genuinely was requested", async () => {
        const [alpha, ...rest] = mockRecords()
        const requested = {
            ...alpha,
            thermo_ref: "thm_requested",
            temperature_coverage: {
                ...alpha.temperature_coverage,
                requested_min_k: 200, requested_max_k: 2000,
                covers_requested_range: false, extrapolation_distance_k: 50,
            },
        }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [requested, ...rest] }))))
        page()
        const card = (await screen.findByText("thm_requested")).closest("article") as HTMLElement
        expect(ddFor(card, "Requested range (K)")).toBe("200–2000")
        expect(ddFor(card, "Covers requested range")).toBe("No")
        expect(ddFor(card, "Extrapolation distance (K)")).toBe("50")
    })

    // Finding 16: "MODEL KIND: nasa" under a heading that already says
    // "NASA-7 thermo record" repeats the exact word the heading just used.
    it("never prints a 'Model kind' row that repeats the card's own heading", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        const alphaCard = (await screen.findByText("thm_alpha")).closest("article") as HTMLElement
        expect(within(alphaCard).queryByText("Model kind")).not.toBeInTheDocument()
        // The heading itself is unaffected -- this isn't "nothing about
        // the model kind renders at all".
        expect(within(alphaCard).getByText("NASA-7 thermo record")).toBeVisible()
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

    // Finding 16: an empty "Group-additivity estimation" box rendered on
    // every card, including `nasa`/computed records that were never going
    // to have one — group-additivity is a scheme only an ESTIMATED record
    // ever carries at all, unlike nasa/nasa9/wilhoit/points (the four
    // possible SHAPES of the one model kind every record declares). The
    // section renders only for the record that actually has it now.
    it("renders group-additivity data only for the record that has it, and renders nothing at all (no section) for one that doesn't", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_beta")
        const betaCard = screen.getByText("thm_beta").closest("article") as HTMLElement
        expect(within(betaCard).getByText("Group-additivity estimation")).toBeVisible()
        expect(within(betaCard).getByText("Benson v2 (2.0)")).toBeVisible()
        expect(within(betaCard).getByText("C/H3")).toBeVisible()

        // thm_alpha has group_additivity: null — no section at all, not a
        // heading over an explicit "not recorded" line.
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(within(alphaCard).queryByText("Group-additivity estimation")).not.toBeInTheDocument()
        expect(within(alphaCard).queryByText("No group-additivity estimation recorded for this record.")).not.toBeInTheDocument()
    })

    // Finding 16: PRIMARY CALCULATION and SINGLE-POINT CALCULATION used to
    // list the same ref twice, under two separate headings, whenever a
    // record's SP energy came from its own optimization
    // (`feedback_sp_vs_opt_energy`) -- reading as though two different
    // calculations happened to match. thm_alpha's fixture is deliberately
    // that case (`primary_calculation.calculation_ref` ===
    // `sp_calculation_ref` === "calc_alpha_sp"): the two roles now merge
    // into ONE row, said once.
    it("merges Primary calculation and Single-point calculation into one row when they cite the SAME calculation ref, and links each distinct ref once", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement

        // calc_alpha_sp: exactly ONE link, under a row naming BOTH roles.
        const spLinks = within(alphaCard).getAllByRole("link", { name: "calc_alpha_sp" })
        expect(spLinks).toHaveLength(1)
        expect(spLinks[0]).toHaveAttribute("href", "/calculations/calc_alpha_sp")
        expect(ddFor(alphaCard, "Primary calculation / Single-point calculation")).toBe("calc_alpha_sp")
        expect(within(alphaCard).queryByText("Primary calculation")).not.toBeInTheDocument()
        expect(within(alphaCard).queryByText("Single-point calculation")).not.toBeInTheDocument()

        // calc_alpha_freq is a genuinely DIFFERENT calculation -- its own
        // row, own link, never folded into the merged row above.
        expect(within(alphaCard).getByRole("link", { name: "calc_alpha_freq" })).toHaveAttribute("href", "/calculations/calc_alpha_freq")
        expect(ddFor(alphaCard, "Frequency calculation")).toBe("calc_alpha_freq")

        expect(within(alphaCard).getByText("sm_alpha")).toBeVisible()
        expect(within(alphaCard).queryByRole("link", { name: "sm_alpha" })).not.toBeInTheDocument()
    })

    it("keeps Primary/Frequency/Single-point calculation as three separate rows when all three cite different refs", async () => {
        const [, beta] = mockRecords()
        const distinctRefs = {
            ...beta,
            thermo_ref: "thm_distinct_refs",
            provenance: {
                ...beta.provenance,
                primary_calculation: { calculation_ref: "calc_primary", calculation_type: "opt", converged: true, geometry_validation_status: "not_present", scf_stability_status: "not_present", level_of_theory: null, software: null },
                freq_calculation_ref: "calc_freq",
                sp_calculation_ref: "calc_sp",
            },
        }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [distinctRefs] }))))
        page()
        const card = (await screen.findByText("thm_distinct_refs")).closest("article") as HTMLElement
        expect(ddFor(card, "Primary calculation")).toBe("calc_primary")
        expect(ddFor(card, "Frequency calculation")).toBe("calc_freq")
        expect(ddFor(card, "Single-point calculation")).toBe("calc_sp")
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

    it("never falls back to the calculation's software when the thermo's own is absent (issue #284, population A)", async () => {
        // thm_alpha IS population A: primary_calculation.software is a real
        // Gaussian summary, but provenance.software_release is null. A
        // fixture where BOTH are null (as every other one in this file is)
        // cannot catch a fallback from software_release to
        // primary_calculation.software -- it never fires when there's
        // nothing to fall back to. This is the one fixture with the
        // combination that matters: thermo software absent, calculation
        // software present.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement
        expect(ddFor(alphaCard, "Software")).toBe("not recorded")
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

    // The owner's report, thermo half: "he opened the Thermochemistry tab
    // having selected conformer_2, and what sits there is another
    // conformer's record under a heading he has to read carefully to
    // notice." thm_alpha's own fixture traces to no primary calculation
    // conformer at all here; give it one that names conformer_1, select
    // conformer_2, and the panel must say plainly that conformer_2 has
    // nothing -- never silently show thm_alpha as though it belonged there.
    it("says plainly when the selected conformer has no thermo record, and demotes (never deletes) a record traced to a different one", async () => {
        const [alpha, ...rest] = mockRecords()
        const tracedToOne = { ...alpha, provenance: { ...alpha.provenance, conformer_group_ref: "cg_one" } }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [tracedToOne, ...rest.map((r) => ({ ...r, provenance: { ...r.provenance, conformer_group_ref: null } })) ] }))))
        page(conformerGroups[1], conformerGroups) // conformer_2 selected
        await screen.findByRole("heading", { name: "From Conformer Group 2" })

        const answer = screen.getByText("No thermo record traces to this conformer yet.")
        expect(answer).toBeVisible()
        expect(answer).toHaveClass("conformer-attribution-answer")
        const primaryGroup = screen.getByRole("heading", { name: "From Conformer Group 2" }).closest(".conformer-evidence-group") as HTMLElement
        expect(within(primaryGroup).queryByText("thm_alpha")).not.toBeInTheDocument()

        // thm_alpha is demoted, not deleted: reachable inside the collapsed
        // other-conformers disclosure.
        const otherDetails = document.querySelector(".conformer-attribution-other") as HTMLDetailsElement
        expect(otherDetails).not.toBeNull()
        expect(otherDetails.open).toBe(false)
        expect(within(otherDetails).getByText("thm_alpha")).toBeInTheDocument()
        expect(within(otherDetails).getByRole("heading", { name: "From Conformer Group 1" })).toBeInTheDocument()
    })

    it("renders the selected conformer's own thermo record plainly, with no other-conformers disclosure at all, when it's the only one that traces anywhere", async () => {
        const [alpha, ...rest] = mockRecords()
        const tracedToTwo = { ...alpha, provenance: { ...alpha.provenance, conformer_group_ref: "cg_two" } }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [tracedToTwo, ...rest.map((r) => ({ ...r, provenance: { ...r.provenance, conformer_group_ref: null } })) ] }))))
        page(conformerGroups[1], conformerGroups) // conformer_2 selected
        await screen.findByRole("heading", { name: "From Conformer Group 2" })
        const primaryGroup = screen.getByRole("heading", { name: "From Conformer Group 2" }).closest(".conformer-evidence-group") as HTMLElement
        expect(within(primaryGroup).getByText("thm_alpha")).toBeVisible()
        expect(document.querySelector(".conformer-attribution-other")).toBeNull()
    })

    // Test gap the owner flagged: `renderThermoRecords` (built by
    // `makeThermoGroupedRenderer`) groups by scientific fingerprint INSIDE
    // whatever record list it's called with -- and `ConformerAttributionGroups`
    // calls it once per bucket (this-conformer / other-conformer / no-link),
    // never once over the whole entry. No test proved that a record traced
    // to a DIFFERENT conformer than the selected one, but reporting
    // IDENTICAL H298/S298/NASA-7 values, stays its own ungrouped card in
    // its OWN bucket rather than being folded into a cross-bucket
    // "2 records with identical values" group.
    it("never groups two identical-value records together when they trace to DIFFERENT conformers -- each bucket groups its own records only", async () => {
        const [alpha] = mockRecords()
        const tracedToOne = { ...alpha, thermo_ref: "thm_g1", provenance: { ...alpha.provenance, conformer_group_ref: "cg_one" } }
        const tracedToTwo = { ...alpha, thermo_ref: "thm_g2", provenance: { ...alpha.provenance, conformer_group_ref: "cg_two" } }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [tracedToOne, tracedToTwo] }))))
        page(conformerGroups[0], conformerGroups) // conformer_1 selected

        await screen.findByRole("heading", { name: "From Conformer Group 1" })

        // The selected conformer's own bucket shows ONLY its own record,
        // as a plain (ungrouped) card -- never the "N records with
        // identical values" wrapper a same-bucket duplicate would get.
        const primaryGroup = screen.getByRole("heading", { name: "From Conformer Group 1" }).closest(".conformer-evidence-group") as HTMLElement
        expect(within(primaryGroup).getByText("thm_g1")).toBeVisible()
        expect(within(primaryGroup).queryByText("thm_g2")).not.toBeInTheDocument()
        expect(within(primaryGroup).queryByText(/records with identical values/)).not.toBeInTheDocument()

        // The other-conformer record is demoted into its own collapsed
        // disclosure, same as any other cross-conformer record -- not
        // merged into the primary bucket's card just because the two
        // report the same numbers.
        const otherDetails = document.querySelector(".conformer-attribution-other") as HTMLDetailsElement
        expect(otherDetails).not.toBeNull()
        expect(within(otherDetails).getByText("thm_g2")).toBeInTheDocument()
        expect(within(otherDetails).queryByText("thm_g1")).not.toBeInTheDocument()
        expect(within(otherDetails).queryByText(/records with identical values/)).not.toBeInTheDocument()

        // Nowhere on the page do the two get grouped under one card --
        // the bug this test guards against would show exactly this text.
        expect(screen.queryByText(/records with identical values/)).not.toBeInTheDocument()
    })
})

// ---------------------------------------------------------------------------
// SHOULD-FIX-9 (species-entry/browse/chrome residuals re-review): the
// provenance block used to render "Level of theory ref"/"Statmech ref" as
// plain 15px sans text, next to `.data` calc refs two rows below on the
// same card -- a ref is a ref regardless of whether this page happens to
// link it.
// ---------------------------------------------------------------------------
describe("EntryThermoSection: provenance refs render as .data, like every other ref on the card", () => {
    it("Level of theory ref and Statmech ref are wrapped in .data when present", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        const alphaCard = screen.getByText("thm_alpha").closest("article") as HTMLElement

        const lotDt = Array.from(alphaCard.querySelectorAll("dt")).find((el) => el.textContent === "Level of theory ref")!
        const lotDd = lotDt.nextElementSibling as HTMLElement
        expect(lotDd).toHaveTextContent("lot_alpha")
        expect(lotDd.querySelector(".data")).not.toBeNull()

        const statmechDt = Array.from(alphaCard.querySelectorAll("dt")).find((el) => el.textContent === "Statmech ref")!
        const statmechDd = statmechDt.nextElementSibling as HTMLElement
        expect(statmechDd).toHaveTextContent("sm_alpha")
        expect(statmechDd.querySelector(".data")).not.toBeNull()
    })

    it("renders plain 'not recorded' text (no empty .data span) when either ref is absent", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_beta")
        const betaCard = screen.getByText("thm_beta").closest("article") as HTMLElement

        const lotDt = Array.from(betaCard.querySelectorAll("dt")).find((el) => el.textContent === "Level of theory ref")!
        const lotDd = lotDt.nextElementSibling as HTMLElement
        expect(lotDd).toHaveTextContent("not recorded")
        expect(lotDd.querySelector(".data")).toBeNull()
    })
})

// ---------------------------------------------------------------------------
// The owner: "in Thermochemistry tab, I expect the ToC to show NASA-7 Thermo
// record (for example) if it exists as a point to go to." `thm_alpha` in
// `mockRecords()` is nasa/NASA-7, `thm_beta` is nasa9/NASA-9, `thm_gamma` is
// wilhoit -- a fixture already shaped so a "which model kind is present"
// mistake (registering by `model_kind` alone rather than by whether the
// matching data block is actually non-null) would be unobservable no
// differently from a correct implementation, since every record here HAS
// the data its own `model_kind` names. See `EntryThermoSection.tsx`'s
// `hasModelKindData` for the case that isn't true of every record on the
// live archive (a declared kind outliving its data).
// ---------------------------------------------------------------------------
describe("EntryThermoSection: thermo records register in the ToC", () => {
    it("registers a NASA-7 thermo record as a ToC entry, linking to a real element id, when the record actually has NASA-7 data", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        pageWithToc()
        await screen.findByText("thm_alpha")

        // `findByRole`, not `getByRole`: the record text above renders on
        // the data commit, but a ToC entry registers from an effect that
        // lands a tick later -- under full-suite load that tick was
        // sometimes still pending, and the sync query missed the link.
        const link = await screen.findByRole("link", { name: "NASA-7 thermo record" })
        expect(link).toHaveAttribute("href", "#thermo-heading-thm_alpha")
        // The href doesn't merely look plausible -- the id it points to is
        // a real element actually on the page.
        expect(document.getElementById("thermo-heading-thm_alpha")).not.toBeNull()

        // The two other present model kinds register too, each under its
        // own label -- not just NASA-7.
        expect(await screen.findByRole("link", { name: "NASA-9 thermo record" }))
            .toHaveAttribute("href", "#thermo-heading-thm_beta")
        expect(await screen.findByRole("link", { name: "Wilhoit thermo record" }))
            .toHaveAttribute("href", "#thermo-heading-thm_gamma")
    })

    it("registers no NASA-7 ToC entry when no deposited record actually has NASA-7 data", async () => {
        // thm_gamma alone: wilhoit only, no nasa/nasa9/points anywhere in
        // this response.
        const [, , gamma] = mockRecords()
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [gamma] }))))
        pageWithToc()
        await screen.findByText("thm_gamma")

        // Positive check FIRST, and awaited: the ONE present model kind is
        // in the ToC, so registration has actually run by the time the
        // absence below is asserted. Asserting the absence before that
        // wait would pass trivially on an unregistered nav.
        expect(await screen.findByRole("link", { name: "Wilhoit thermo record" })).toBeVisible()
        expect(screen.queryByRole("link", { name: /NASA-7/ })).not.toBeInTheDocument()
    })

    it("does not register a ToC entry for a record whose declared model_kind has no matching data -- a claimed kind is not the same as data", async () => {
        // A record that CLAIMS nasa but whose `nasa` field is null -- the
        // defensive case `NasaBlock` already renders around ("No NASA-7
        // polynomial recorded for this record."). Checking `model_kind`
        // alone (ignoring whether the data is actually there) would
        // register this as a ToC entry pointing at that exact empty
        // message -- an empty destination, which the design brief
        // explicitly forbids.
        const [alpha] = mockRecords()
        const claimsNasaButEmpty = { ...alpha, thermo_ref: "thm_empty_nasa", nasa: null }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [claimsNasaButEmpty] }))))
        pageWithToc()
        await screen.findByText("thm_empty_nasa")

        expect(screen.queryByRole("link", { name: /NASA-7/ })).not.toBeInTheDocument()
        // Positive: the record itself still rendered (its own defensive
        // "No NASA-7 polynomial recorded" message), so this isn't "nothing
        // rendered at all" passing for the wrong reason.
        expect(screen.getByText("No NASA-7 polynomial recorded for this record.")).toBeVisible()
    })

    it("disambiguates two records of the same model kind rather than repeating one label", async () => {
        const [alpha, beta, gamma] = mockRecords()
        const secondNasa = { ...gamma, thermo_ref: "thm_delta", model_kind: "nasa", nasa: alpha.nasa, nasa9: null, wilhoit: null, points: null }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [alpha, beta, gamma, secondNasa] }))))
        pageWithToc()
        await screen.findByText("thm_delta")

        const first = await screen.findByRole("link", { name: "NASA-7 thermo record 1" })
        const second = await screen.findByRole("link", { name: "NASA-7 thermo record 2" })
        // Absence asserted after the positive waits, so the nav has registered.
        expect(screen.queryByRole("link", { name: "NASA-7 thermo record" })).not.toBeInTheDocument()
        expect(first).toHaveAttribute("href", "#thermo-heading-thm_alpha")
        expect(second).toHaveAttribute("href", "#thermo-heading-thm_delta")
    })
})

// ---------------------------------------------------------------------------
// Finding 7 of the block review: "seven byte-identical thermo records
// render as fourteen full cards" -- 12,000px of scrolling to learn one
// fact. `domain/identicalRecordGroups.ts`'s `thermoRecordFingerprint`
// compares model_kind, scientific_origin, H298/S298 (+ uncertainties), the
// full model block (nasa/nasa9/wilhoit/points), and the record's own
// temperature range -- never ref, date, review status, or provenance.
// ---------------------------------------------------------------------------
describe("EntryThermoSection: identical-value records group under one card", () => {
    /** Three clones of thm_alpha, identical in every fingerprinted field,
     *  differing only in `thermo_ref` and one PROVENANCE field
     *  (`provenance.software_release`) -- the review's own worked case:
     *  provenance that differs across identical-value records must stay
     *  visible even though the records are grouped. */
    function identicalClones() {
        const [alpha] = mockRecords()
        return [
            { ...alpha, thermo_ref: "thm_g1", provenance: { ...alpha.provenance, software_release: null } },
            { ...alpha, thermo_ref: "thm_g2", provenance: { ...alpha.provenance, software_release: null } },
            { ...alpha, thermo_ref: "thm_g3", provenance: { ...alpha.provenance, software_release: { software_release_ref: "srel_arkane", software: "Arkane", version: "1.0" } } },
        ]
    }

    it("groups records reporting identical H298/S298/NASA-7 coefficients under one card, listing every ref", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: identicalClones() }))))
        page()
        await screen.findByText("3 records with identical values")

        // Exactly ONE group card -- never three separate ones.
        expect(document.querySelectorAll("article.identical-record-group")).toHaveLength(1)
        const groupCard = document.querySelector("article.identical-record-group") as HTMLElement
        // The group's OWN heading, scoped to its own heading row (not the
        // three member headings nested inside the collapsed "show all"
        // detail -- jsdom's default queries do not filter those out by
        // visibility, so this reads the DOM structure directly instead).
        const ownHeadingRow = groupCard.querySelector(":scope > .science-record-heading") as HTMLElement
        expect(ownHeadingRow.querySelector("h3")?.textContent).toBe("NASA-7 thermo record")
        // Every ref is still listed, in the always-visible group-refs table
        // (not hidden behind "show all").
        const refsTable = within(groupCard).getByRole("table", { name: "Records sharing these identical values" })
        expect(within(refsTable).getByText("thm_g1")).toBeVisible()
        expect(within(refsTable).getByText("thm_g2")).toBeVisible()
        expect(within(refsTable).getByText("thm_g3")).toBeVisible()
        // The shared scientific content itself renders once in the group's
        // own body (outside the collapsed detail) -- not once per record.
        const detail = groupCard.querySelector(".identical-record-group-detail") as HTMLElement
        const bodyOutsideDetail = Array.from(groupCard.querySelectorAll("dd"))
            .filter((el) => !detail.contains(el))
            .filter((el) => el.textContent === "111.10 kJ/mol")
        expect(bodyOutsideDetail).toHaveLength(1)
    })

    it("keeps provenance that differs across an identical-value group visible per ref, never collapsed away by grouping", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: identicalClones() }))))
        page()
        await screen.findByText("3 records with identical values")
        const refsTable = screen.getByRole("table", { name: "Records sharing these identical values" })
        const rows = within(refsTable).getAllByRole("row").slice(1)
        const g1Row = rows.find((row) => within(row).queryByText("thm_g1"))!
        const g3Row = rows.find((row) => within(row).queryByText("thm_g3"))!
        expect(cellAt(g1Row, "Software")).toBe("not recorded")
        expect(cellAt(g3Row, "Software")).toBe("Arkane 1.0")
    })

    it("show-all is a real disclosure, closed by default, that mounts every member's own full card underneath the group card", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: identicalClones() }))))
        page()
        await screen.findByText("3 records with identical values")

        const detail = screen.getByText("Show all 3 records individually").closest("details") as HTMLDetailsElement
        expect(detail.open).toBe(false)
        // Three member cards mounted inside it -- one full `<article>` per
        // record in the group, each carrying its own ref as a `<code>`.
        const memberCards = within(detail).getAllByRole("article") as HTMLElement[]
        expect(memberCards).toHaveLength(3)
        const memberRefs = memberCards.map((card) => card.querySelector("code")?.textContent)
        expect(memberRefs.sort()).toEqual(["thm_g1", "thm_g2", "thm_g3"])

        fireEvent.click(screen.getByText("Show all 3 records individually"))
        expect(detail.open).toBe(true)
    })

    it("never wraps a lone record in a '1 identical' group -- a single record renders as a plain card", async () => {
        const [alpha] = mockRecords()
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: [alpha] }))))
        page()
        await screen.findByText("thm_alpha")
        expect(screen.queryByText(/records with identical values/)).not.toBeInTheDocument()
        expect(screen.queryByText(/Show all \d+ records individually/)).not.toBeInTheDocument()
        expect(screen.queryByText("Records in this group")).not.toBeInTheDocument()
    })

    it("keeps records that differ in ANY scientific value as separate cards, never grouped", async () => {
        // The file's own mockRecords() fixture: three records deliberately
        // differing in every field under test (see its own docstring) --
        // no grouping should occur at all.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        expect(screen.queryByText(/records with identical values/)).not.toBeInTheDocument()
        expect(screen.getByText("thm_alpha")).toBeVisible()
        expect(screen.getByText("thm_beta")).toBeVisible()
        expect(screen.getByText("thm_gamma")).toBeVisible()
    })

    /**
     * Three clones sharing every fingerprinted field (including LoT) but
     * citing THREE DIFFERENT primary/freq/SP calculations and three
     * different statmech refs -- the live bug this fix was written
     * against: the group card previously rendered `records[0]`'s
     * provenance under an unqualified "Provenance" heading as though it
     * held for all three, which is false the moment the records cite
     * different calculations (as the real ethene entry's 7 thermo records
     * do).
     */
    function clonesWithDifferentCalculations() {
        const [alpha] = mockRecords()
        return ["c1", "c2", "c3"].map((suffix) => ({
            ...alpha,
            thermo_ref: `thm_${suffix}`,
            provenance: {
                ...alpha.provenance,
                primary_calculation: { ...alpha.provenance.primary_calculation, calculation_ref: `calc_${suffix}_primary` },
                freq_calculation_ref: `calc_${suffix}_freq`,
                sp_calculation_ref: `calc_${suffix}_sp`,
                statmech_ref: `sm_${suffix}`,
            },
        }))
    }

    it("lists each record's OWN primary/freq/SP calculation and statmech ref in the group table, on the card, without expanding anything -- never one record's provenance standing in for the whole group", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: clonesWithDifferentCalculations() }))))
        page()
        await screen.findByText("3 records with identical values")

        const groupCard = document.querySelector("article.identical-record-group") as HTMLElement
        const detail = groupCard.querySelector(".identical-record-group-detail") as HTMLElement
        const refsTable = within(groupCard).getByRole("table", { name: "Records sharing these identical values" })
        // Reachable WITHOUT expanding "Show all" -- the table sits outside
        // the collapsed detail entirely.
        expect(detail.contains(refsTable)).toBe(false)

        const rows = within(refsTable).getAllByRole("row").slice(1)
        const rowFor = (ref: string) => rows.find((row) => within(row).queryByText(ref))!

        for (const suffix of ["c1", "c2", "c3"]) {
            const row = rowFor(`thm_${suffix}`)
            expect(cellAt(row, "Primary calculation")).toBe(`calc_${suffix}_primary`)
            expect(cellAt(row, "Freq calculation")).toBe(`calc_${suffix}_freq`)
            expect(cellAt(row, "SP calculation")).toBe(`calc_${suffix}_sp`)
            expect(cellAt(row, "Statmech ref")).toBe(`sm_${suffix}`)
            // SHOULD-FIX-9 (re-review): the Statmech ref cell renders as
            // `.data` now, like every other ref column in this table.
            const statmechCell = row.querySelector('td[data-label="Statmech ref"]') as HTMLElement
            expect(statmechCell.querySelector(".data")).not.toBeNull()
        }

        // The shared body (outside the table, outside "Show all") never
        // attributes any ONE record's provenance to the group -- no
        // "Provenance" block renders there at all.
        const provenanceHeadingsOutsideDetail = Array.from(groupCard.querySelectorAll("h4"))
            .filter((heading) => heading.textContent === "Provenance" && !detail.contains(heading))
        expect(provenanceHeadingsOutsideDetail).toHaveLength(0)
    })

    /**
     * Owner report: the group-refs table showed the same calculation ref
     * twice, once under "Primary calculation" and again under a second
     * role's column. Measured against the live archive (every deposited
     * thermo record across every species entry, 65 records / 8
     * multi-record groups, curled 2026-09-03) before fixing: it is the SP
     * column that always repeats the Primary ref (an SP-from-optimization
     * record cites the same job for both roles — 65/65), never the Freq
     * column (a frequency job is a structurally separate calculation run
     * — 0/65). The fix is applied to BOTH columns identically rather than
     * hard-coded to SP, so it stays correct if a future record's Freq ref
     * ever does collapse onto Primary; this fixture exercises that Freq
     * "same" branch directly since no live record does today.
     */
    function cloneWithMixedCalculationOverlap() {
        const [alpha] = mockRecords()
        // A second clone, identical in every fingerprinted (H298/S298/
        // NASA-7) field to the first -- required for either row to reach
        // the group-refs table at all (a lone record renders as a plain
        // card with no such table, per the "never wraps a lone record"
        // test above).
        // Also carries a null Freq ref -- the group table's third branch
        // (NIT: no test previously covered "not recorded" in this table).
        const plain = { ...alpha, thermo_ref: "thm_plain", provenance: { ...alpha.provenance, freq_calculation_ref: null } }
        const overlap = {
            ...alpha,
            thermo_ref: "thm_overlap",
            provenance: {
                ...alpha.provenance,
                primary_calculation: { ...alpha.provenance.primary_calculation, calculation_ref: "calc_shared" },
                // SAME as primary -- collapses to "same as primary".
                freq_calculation_ref: "calc_shared",
                // DIFFERENT from primary -- keeps its own ref/link.
                sp_calculation_ref: "calc_distinct_sp",
            },
        }
        return [plain, overlap]
    }

    it("collapses a Freq/SP calculation cell to 'same as primary' when it cites the SAME calculation as Primary, but keeps a differing ref linked in full", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: cloneWithMixedCalculationOverlap() }))))
        page()
        await screen.findByText("2 records with identical values")

        const refsTable = screen.getByRole("table", { name: "Records sharing these identical values" })
        const row = within(refsTable).getByText("thm_overlap").closest("tr") as HTMLElement

        // Primary keeps its own ref, linked -- the anchor every other
        // column is compared against.
        expect(cellAt(row, "Primary calculation")).toBe("calc_shared")
        expect(within(row).getByRole("link", { name: "calc_shared" })).toHaveAttribute("href", "/calculations/calc_shared")

        // Freq cites the IDENTICAL ref -- collapsed to prose, never a
        // second link to the same record, never the raw ref repeated.
        expect(cellAt(row, "Freq calculation")).toBe("same as primary")
        expect(within(row).queryAllByRole("link", { name: "calc_shared" })).toHaveLength(1)

        // SP cites a DIFFERENT ref -- stays its own link, never hidden or
        // collapsed just because a sibling column collapsed.
        expect(cellAt(row, "SP calculation")).toBe("calc_distinct_sp")
        expect(within(row).getByRole("link", { name: "calc_distinct_sp" })).toHaveAttribute("href", "/calculations/calc_distinct_sp")

        // The sibling row's null Freq ref hits the cell's third branch --
        // "not recorded", plain text, never a link and never confused with
        // the "same as primary" collapse (a null ref is not a match).
        const plainRow = within(refsTable).getByText("thm_plain").closest("tr") as HTMLElement
        expect(cellAt(plainRow, "Freq calculation")).toBe("not recorded")
        const plainFreqCell = plainRow.querySelector('td[data-label="Freq calculation"]') as HTMLElement
        expect(within(plainFreqCell).queryByRole("link")).toBeNull()
    })

    it("shows the shared level of theory once on the group card -- it is in the identity fingerprint, so every grouped record has it", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: clonesWithDifferentCalculations() }))))
        page()
        await screen.findByText("3 records with identical values")
        const groupCard = document.querySelector("article.identical-record-group") as HTMLElement
        // Positive: the LoT row is ON the group card, outside the collapsed
        // per-record detail -- not merely "no Provenance heading".
        // Query the row by its own label: the collapsed per-record detail also
        // contains a <dt>Level of theory</dt> inside each ProvenanceBlock, so a
        // text query would match twice.
        const shared = groupCard.querySelector('dl[aria-label="Shared level of theory"]') as HTMLElement | null
        expect(shared).not.toBeNull()
        expect(within(shared as HTMLElement).getByText("Level of theory", { selector: "dt" })).toBeInTheDocument()
        expect((shared as HTMLElement).closest(".identical-record-group-detail")).toBeNull()
    })

    it("never mints a duplicate DOM id between the group card's own elements and the same representative record's card inside 'Show all'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse({ records: identicalClones() }))))
        page()
        await screen.findByText("3 records with identical values")
        // Expand "Show all" so the representative's own (unmodified) card
        // -- and its ids -- are actually mounted alongside the group's.
        fireEvent.click(screen.getByText("Show all 3 records individually"))

        const idCounts = new Map<string, number>()
        document.querySelectorAll("[id]").forEach((el) => {
            idCounts.set(el.id, (idCounts.get(el.id) ?? 0) + 1)
        })
        const duplicates = [...idCounts.entries()].filter(([, count]) => count > 1)
        expect(duplicates).toEqual([])
    })
})

describe("EntryThermoSection: design-system adoption (design/species-entry)", () => {
    // Same four invariants as `EntryStatmechSection.test.tsx`'s matching
    // block, checked against this tab's own disclosures ("Full checklist",
    // "Show all N records individually") and record tables (NASA-7,
    // NASA-9) -- rendering the real component tree, not inferred from
    // source text.

    it("never renders an <h2>/<h3>/<h4> inside a <summary>", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        document.querySelectorAll("details > summary").forEach((summary) => fireEvent.click(summary))
        const headingsInsideSummaries = document.querySelectorAll("summary h1, summary h2, summary h3, summary h4, summary h5, summary h6")
        expect(headingsInsideSummaries).toHaveLength(0)
    })

    // Review finding (SHOULD-FIX 1, PR 366): the "N records · review: …"
    // line carries `.records-note` (layout-only, entry-science.css) at
    // every call site, but must ALSO carry `.note` (design-system.css) --
    // `.records-note` was stripped down to margin-only, so without `.note`
    // this line silently falls back to 16px unstyled body ink with no
    // max-width, on exactly the two tabs (statmech, transport) where the
    // migration was missed.
    it("the 'N records · review: …' line renders through .note, not a bare unstyled paragraph", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        const recordsNote = document.querySelector(".records-note")
        expect(recordsNote).not.toBeNull()
        expect(recordsNote!.className.split(" ")).toContain("note")
    })

    it("every <details> on this tab (Full checklist, evidence-completeness) is the shared Disclosure component -- carries the `disclosure` class", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        const detailsElements = Array.from(document.querySelectorAll("details"))
        expect(detailsElements.length).toBeGreaterThan(0)
        for (const details of detailsElements) {
            expect(details.className.split(" ")).toContain("disclosure")
        }
    })

    it("'not reviewed' renders in exactly one pill style -- .value-pill--muted, never the retired .review-badge", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        const notReviewedPills = screen.getAllByText("not reviewed")
        expect(notReviewedPills.length).toBeGreaterThan(0)
        for (const pill of notReviewedPills) {
            expect(pill.className.split(" ")).toContain("value-pill--muted")
            expect(pill.className.split(" ")).not.toContain("review-badge")
        }
    })

    it("every record table on this tab (NASA-7, NASA-9) is the shared .data-table primitive inside .table-scroll, never the retired .stage-table or a stacked fallback", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("thm_alpha")
        const tables = Array.from(document.querySelectorAll("table"))
        expect(tables.length).toBeGreaterThanOrEqual(2) // NASA-7 (thm_alpha) + NASA-9 (thm_beta)
        for (const table of tables) {
            expect(table.className.split(" ")).toContain("data-table")
            expect(table.className.split(" ")).not.toContain("stage-table")
            expect(table.closest(".table-scroll")).not.toBeNull()
        }
    })
})
