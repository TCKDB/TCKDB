import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import TransitionStateEntryPage from "./TransitionStateEntryPage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const ENTRY_REF = "tse_aq5ktxlu27nvul3hmdwpuyuz4e"

function page(ref = ENTRY_REF) {
    return render(
        <MemoryRouter initialEntries={[`/transition-state-entries/${ref}`]}>
            <Routes>
                <Route path="/transition-state-entries/:entryRef" element={<TransitionStateEntryPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

// Trimmed from a live `GET /api/v1/scientific/transition-state-entries/
// tse_aq5ktxlu27nvul3hmdwpuyuz4e?include=calculations,geometries,review`
// response (curled 2026-09-03) -- field names, nesting and the `has_irc:
// true` / `validation.irc: "absent"` combination (an IRC calculation
// exists, but no *passed evidence record* was deposited for it -- see
// `TransitionStateValidationDescriptor`'s own docstring) are the real
// served shape, not invented. The 18 `irc_reverse` geometries the live
// record actually carries are trimmed to 2 here -- the count is not the
// thing under test.
function mockRecord(overrides: Record<string, unknown> = {}) {
    return {
        transition_state_entry: {
            transition_state_entry_ref: ENTRY_REF,
            charge: 0,
            multiplicity: 2,
            status: "optimized",
            unmapped_smiles: "C1=C[C]2C=CCC2C=C1>>C1=CC2=C(C=C1)CC=C2.[H]",
            created_at: "2026-08-05T14:04:16.914780",
            review: { status: "not_reviewed" },
        },
        transition_state: {
            transition_state_ref: "ts_uql5lf3xeqnehtostrilmns5yi",
            label: "TS0",
            note: null,
            created_at: "2026-08-05T14:04:16.914780",
            review: { status: "not_reviewed" },
        },
        reaction: {
            reaction_ref: "rxn_nu4c52up4c4hqtbtxufwbscq3a",
            reaction_entry_ref: "rxe_snamm2m4daoyiw6ljc3cvmfabm",
            equation: "C1=C[C]2C=CCC2C=C1 <=> C1=Cc2ccccc2C1 + [H]",
            reversible: true,
            family: "R_Addition_MultipleBond",
        },
        evidence_summary: {
            calculation_count: 4,
            has_opt: true,
            has_freq: true,
            has_sp: true,
            has_irc: true,
            has_path_search: false,
            has_geometry_validation: false,
            has_scf_stability: false,
            levels_of_theory: {
                opt: [{ level_of_theory_ref: "lot_rrmbqrod3suvzkez2ta76hj76u", method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }],
                sp: [{ level_of_theory_ref: "lot_rrmbqrod3suvzkez2ta76hj76u", method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }],
            },
        },
        validation: { irc: "absent" },
        available_sections: {
            has_entries: true,
            has_calculations: true,
            has_geometries: true,
            has_review: true,
            has_validation_evidence: false,
        },
        calculations: [
            {
                calculation_ref: "calc_yu6nspewwco74qzh6lwgh6ewxy",
                type: "opt",
                quality: "raw",
                review: { status: "not_reviewed" },
                level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" },
                software_release: { software: "Gaussian" },
                workflow_tool_release: { workflow_tool: "ARC" },
            },
            {
                calculation_ref: "calc_heds3dt3dsqxmqkcchibvbtwi4",
                type: "irc",
                quality: "raw",
                review: { status: "not_reviewed" },
                level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" },
                software_release: { software: "Gaussian" },
                workflow_tool_release: { workflow_tool: "ARC" },
            },
        ],
        geometries: [
            { geometry_ref: "geom_mtihqs7ac7btur4efqushl7aoy", input_order: null, output_order: 1, role: "final", natoms: 18, geom_hash: "03dcc2c3" },
            { geometry_ref: "geom_4gm2gdummrj2kepzxfn3ivq6pu", input_order: null, output_order: 2, role: "irc_reverse", natoms: 18, geom_hash: "2bed5bab" },
        ],
        review_history: [
            { status: "not_reviewed", reviewed_at: null, note: null },
        ],
        ...overrides,
    }
}

describe("TransitionStateEntryPage", () => {
    it("renders the entry's identity from the real API shape and never shows a SMILES row", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include")).toEqual(["calculations", "geometries", "review"])
            return HttpResponse.json({ record: mockRecord() })
        }))

        page()
        expect(await screen.findByRole("heading", { name: "TS0" })).toBeVisible()

        // The unmapped SMILES the API actually serves is shown...
        const unmappedRow = screen.getByText("Unmapped SMILES").closest("div")
        expect(unmappedRow).not.toBeNull()
        expect(within(unmappedRow as HTMLElement).getByText("C1=C[C]2C=CCC2C=C1>>C1=CC2=C(C=C1)CC=C2.[H]")).toBeVisible()

        // ...and no plain "SMILES" row (the species-entry identity field)
        // is ever rendered for a transition state -- it has no canonical
        // SMILES, and this page must not fabricate an empty one.
        expect(screen.queryByText("SMILES", { selector: "dt" })).not.toBeInTheDocument()
        expect(screen.queryByText("InChIKey")).not.toBeInTheDocument()

        // Reaction context the API serves.
        expect(screen.getByText("C1=C[C]2C=CCC2C=C1 <=> C1=Cc2ccccc2C1 + [H]")).toBeVisible()
        expect(screen.getByText("R Addition MultipleBond")).toBeVisible()
        // Two links share this name here: the reaction-context section's
        // own, and the (present-but-collapsed) References disclosure row --
        // both must point at the reaction record.
        for (const link of screen.getAllByRole("link", { name: "rxn_nu4c52up4c4hqtbtxufwbscq3a" })) {
            expect(link).toHaveAttribute("href", "/reactions/rxn_nu4c52up4c4hqtbtxufwbscq3a")
        }

        // Review status -- the entry's own badge in the header, and the
        // transition-state concept's (separately reviewable) status in the
        // context row below it. Scoped to `.basin-header`, since "not
        // reviewed" also legitimately repeats per-calculation and in the
        // review-history row further down the page.
        const header = document.querySelector(".basin-header") as HTMLElement
        expect(within(header).getAllByText("not reviewed")).toHaveLength(2)
    })

    it("distinguishes an IRC calculation existing from a passed IRC validation record", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: "TS0" })
        // has_irc: true, validation.irc: "absent" -- the served combination
        // this endpoint measured for tse_aq5ktxlu27nvul3hmdwpuyuz4e. The
        // page must say BOTH things, not collapse them into one claim.
        expect(screen.getByText(/irc yes/)).toBeVisible()
        expect(screen.getByText("not established")).toBeVisible()
        expect(screen.getByText(/An IRC calculation exists on this entry, but no structured pass\/fail evidence/))
            .toBeVisible()
    })

    it("lists calculation and geometry evidence linking to their own record pages", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: "TS0" })

        expect(screen.getByRole("link", { name: "calc_yu6nspewwco74qzh6lwgh6ewxy" }))
            .toHaveAttribute("href", "/calculations/calc_yu6nspewwco74qzh6lwgh6ewxy")
        expect(screen.getByRole("link", { name: "calc_heds3dt3dsqxmqkcchibvbtwi4" }))
            .toHaveAttribute("href", "/calculations/calc_heds3dt3dsqxmqkcchibvbtwi4")

        // The final (saddle-point) geometry is always visible, led with its
        // own label. The single `irc_reverse` point sits behind its
        // direction's disclosure (see the dedicated geometry-wall test
        // below for the collapsed/expanded behaviour itself) -- named on
        // the closed summary by role and count, not opened by default.
        expect(screen.getByRole("link", { name: "geom_mtihqs7ac7btur4efqushl7aoy" }))
            .toHaveAttribute("href", "/geometries/geom_mtihqs7ac7btur4efqushl7aoy")
        expect(screen.getByText(/^final/)).toBeVisible()
        expect(screen.getByText(/IRC reverse.*1 point/)).toBeVisible()
    })

    it("keeps the transition-state ref behind a References disclosure, collapsed by default", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: "TS0" })
        // The `ts_…` ref lives only inside `RefsDisclosure` (unlike the
        // `tse_…` entry ref, which also renders in the always-visible
        // identity header) -- collapsed-but-present, then genuinely
        // visible after opening the toggle, is the real proof this page
        // keeps it behind "References (4)" rather than open at rest.
        // (A closed `<details>` keeps its content in the DOM -- jest-dom's
        // `toBeVisible` is what actually understands that state, not
        // `queryByText`/`toBeInTheDocument`, which would find it either way.)
        expect(screen.getByText("ts_uql5lf3xeqnehtostrilmns5yi")).not.toBeVisible()
        await userEvent.setup().click(screen.getByText(/References \(4\)/))
        expect(screen.getByText("ts_uql5lf3xeqnehtostrilmns5yi")).toBeVisible()
        // Two links now share this name: the reaction-context section's own
        // (always visible) and the opened disclosure's ref row -- both must
        // point at the reaction record.
        for (const link of screen.getAllByRole("link", { name: "rxn_nu4c52up4c4hqtbtxufwbscq3a" })) {
            expect(link).toHaveAttribute("href", "/reactions/rxn_nu4c52up4c4hqtbtxufwbscq3a")
        }
    })

    it("shows a not-found style message for an unknown entry ref", async () => {
        server.use(http.get("/api/v1/scientific/transition-state-entries/tse_missing", () => (
            HttpResponse.json({ detail: "not found" }, { status: 404 })
        )))
        page("tse_missing")
        expect(await screen.findByText("Transition state entry not found")).toBeVisible()
    })

    // Owner report: 'TS0' appeared in the page's own `<h1>` AND again in
    // `RecordIdentityHeader`'s formula slot (which used to fall back to the
    // label when `formula` was null, since a TS never carries one), and
    // 'Charge / multiplicity — 0 / doublet (2)' appeared once in the
    // header's own identity facts and again in the page's `basin-context`.
    // Fixed by dropping the identity header's label fallback (a TS has no
    // formula; the page's `<h1>` already carries the label) and dropping
    // the page's own duplicate charge/multiplicity row (the identity
    // header already carries it). Every fact now renders exactly once.
    it("states the label and the charge/multiplicity fact exactly once each", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: "TS0" })

        expect(screen.getAllByText("TS0")).toHaveLength(1)
        expect(screen.getAllByText((_, node) => node?.tagName === "DD" && node.textContent === "0 / doublet (2)"))
            .toHaveLength(1)
    })

    // Owner report: the geometry section rendered every served geometry as
    // an undifferentiated 5-column grid of `geom_` links -- on the live
    // record, 1 `final` + 50 `irc_forward` + 33 `irc_reverse`, ~1,900px in
    // which the one saddle-point geometry was indistinguishable from the
    // IRC trajectory around it. Fixed by leading with `final`, prominently
    // and linked, then collapsing each IRC direction behind its own
    // disclosure showing a point count rather than one card per point.
    it("leads with the final geometry and collapses each IRC direction behind a disclosure showing its point count", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    geometries: [
                        { geometry_ref: "geom_final", input_order: null, output_order: 1, role: "final", natoms: 18, geom_hash: "a" },
                        { geometry_ref: "geom_fwd_1", input_order: null, output_order: 2, role: "irc_forward", natoms: 18, geom_hash: "b" },
                        { geometry_ref: "geom_fwd_2", input_order: null, output_order: 3, role: "irc_forward", natoms: 18, geom_hash: "c" },
                        { geometry_ref: "geom_fwd_3", input_order: null, output_order: 4, role: "irc_forward", natoms: 18, geom_hash: "d" },
                        { geometry_ref: "geom_rev_1", input_order: null, output_order: 5, role: "irc_reverse", natoms: 18, geom_hash: "e" },
                        { geometry_ref: "geom_rev_2", input_order: null, output_order: 6, role: "irc_reverse", natoms: 18, geom_hash: "f" },
                    ],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "TS0" })

        // The final geometry is rendered prominently -- under its own
        // "Saddle-point geometry" label -- and linked to its record page.
        expect(screen.getByText("Saddle-point geometry")).toBeVisible()
        expect(screen.getByRole("link", { name: "geom_final" })).toHaveAttribute("href", "/geometries/geom_final")

        // The two IRC directions are two disclosures, each naming its own
        // point count -- not six individual geometry cards.
        expect(screen.getByText(/IRC forward.*3 points/)).toBeVisible()
        expect(screen.getByText(/IRC reverse.*2 points/)).toBeVisible()

        // Their contents are collapsed at rest...
        expect(screen.getByRole("link", { name: "geom_fwd_1" })).not.toBeVisible()
        expect(screen.getByRole("link", { name: "geom_rev_1" })).not.toBeVisible()

        // ...but present in the DOM, and become visible once opened.
        for (const summary of screen.getAllByText(/IRC (forward|reverse)/)) {
            await userEvent.setup().click(summary)
        }
        expect(screen.getByRole("link", { name: "geom_fwd_1" })).toBeVisible()
        expect(screen.getByRole("link", { name: "geom_rev_1" })).toBeVisible()
    })

    // Owner report: `software_release.version` and `workflow_tool_release
    // .version` ('Gaussian 16', 'ARC 1.1.0') are served by this endpoint and
    // are load-bearing provenance in this archive, but the calculation
    // table showed only the bare names ('Gaussian', 'ARC'). Fixed by
    // parsing the version through `softwareLabel`/`toolReleaseLabel`, the
    // same helpers every other record page already uses for this.
    it("shows the software and workflow-tool versions in the calculation table", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: [{
                        calculation_ref: "calc_versioned",
                        type: "opt",
                        quality: "raw",
                        review: { status: "not_reviewed" },
                        level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" },
                        software_release: { software: "Gaussian", version: "16" },
                        workflow_tool_release: { workflow_tool: "ARC", version: "1.1.0" },
                    }],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "TS0" })

        expect(screen.getByText(/Gaussian 16/)).toBeVisible()
        expect(screen.getByText(/ARC 1\.1\.0/)).toBeVisible()
    })
})
