import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import TransitionStateEntryPage, { CALCULATION_ROW_ORDER_NOTE } from "./TransitionStateEntryPage"
import { bySummaryText } from "../test/disclosureQueries"
import { ADVERSARIAL, expectDepositorTextOnlyInNotes, expectNoAdversarialText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
import { rankingWords, textOf } from "../test/rankingWords"
import { titleHasKind } from "../test/routeTitleKinds"

const ENTRY_REF = "tse_aq5ktxlu27nvul3hmdwpuyuz4e"

// A default empty-siblings handler so every test gets one without having
// to register it individually -- `resetHandlers()` (no arguments) restores
// exactly this initial list between tests, so a test's own `server.use`
// override for the entry endpoint layers on top without removing it.
// Tests that care about the siblings section override this one directly.
const defaultSiblingsHandler = http.get(
    "/api/v1/scientific/transition-states/search",
    () => HttpResponse.json({ records: [] }),
)

const DEFINITION_TEXT = "each molecule's lowest computed energy at this level, from this program"
const REACTION_ENTRY_REF = "rxe_snamm2m4daoyiw6ljc3cvmfabm"

function participant(ref: string, smiles: string, formula: string, index: number) {
    return { species_entry_ref: ref, smiles, formula, stoichiometry: 1, participant_index: index, review: { status: "not_reviewed" } }
}

// The parent reaction entry's participants: what names the reaction on this page.
function participantsRead() {
    return {
        reaction_entry: {
            reaction_entry_ref: REACTION_ENTRY_REF,
            deposit_position: 2,
            reaction_ref: "rxn_nu4c52up4c4hqtbtxufwbscq3a",
            equation: "as deposited",
            reversible: false,
            family: null,
            review: { status: "not_reviewed" },
            atom_maps: [],
        },
        review_summary: { total: 1, not_reviewed: 1, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
        species: {
            reactants: [participant("spe_ch4", "C", "CH4", 0), participant("spe_oh", "[OH]", "OH", 1)],
            products: [participant("spe_ch3", "[CH3]", "CH3", 0), participant("spe_h2o", "O", "H2O", 1)],
        },
    }
}

const LEVEL = { level_of_theory_ref: "lot_a", method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }

function point(calc: string, hartree: number) {
    return { calculation_ref: calc, energy_type: "single_point", electronic_energy_hartree: hartree, software: { program: "Gaussian", version: "16" }, review_status: "not_reviewed", convergence_unrecorded: false }
}

function profileFor(tse: string, kj: number) {
    return {
        transition_state_entry_ref: tse,
        transition_state_entry_review_status: "not_reviewed",
        level_of_theory: LEVEL,
        program: "Gaussian",
        energy_types: ["single_point"],
        versions: ["16"],
        energy_kind: "electronic",
        definition: DEFINITION_TEXT,
        reactants: [{ species_entry_ref: "spe_ch4", count: 1, ...point("calc_ra", -40.1) }, { species_entry_ref: "spe_oh", count: 1, ...point("calc_rb", -75.2) }],
        transition_state: { transition_state_entry_ref: tse, ...point(`calc_ts_${tse}`, -115.2) },
        products: [{ species_entry_ref: "spe_ch3", count: 1, ...point("calc_pa", -39.5) }, { species_entry_ref: "spe_h2o", count: 1, ...point("calc_pb", -76.3) }],
        energy_hartree: { reactants: -115.3, transition_state: -115.2, products: -115.8 },
        relative_kj_mol: { reactants: 0, transition_state: kj, products: -80 },
    }
}

function energyRead(overrides: Record<string, unknown> = {}) {
    return {
        request: { hidden_by_default: null, hidden_by_default_by_scope: {} },
        reaction_entry_ref: REACTION_ENTRY_REF,
        profiles: [profileFor(ENTRY_REF, 84.3), profileFor("tse_sibling", 12.5)],
        unavailable: [],
        cited_by_kinetics: [],
        ...overrides,
    }
}

const PICTURE = {
    geometry_ref: "geom_mtihqs7ac7btur4efqushl7aoy", natoms: 2, geom_hash: "h", format: "cartesian", coordinate_units: "angstrom", created_at: "2026-01-01T00:00:00Z",
    atoms: [{ atom_index: 1, element: "C", x: 0, y: 0, z: 0 }, { atom_index: 2, element: "O", x: 1.4, y: 0, z: 0 }], xyz_text: null,
}

// Default reads for the parent reaction entry and the picture, so a test overrides only what it is about.
const defaultParentHandlers = [
    http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/full`, () => HttpResponse.json(participantsRead())),
    http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/energy-profile`, () => HttpResponse.json(energyRead())),
    http.get("/api/v1/scientific/geometries/:ref", () => HttpResponse.json(PICTURE)),
]

const server = setupServer(defaultSiblingsHandler, ...defaultParentHandlers)
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

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
// tse_aq5ktxlu27nvul3hmdwpuyuz4e?include=calculations,geometries,review,trust`
// response -- field names, nesting and the `has_irc:
// true` / `validation.irc: "absent"` combination (an IRC calculation
// exists, but no *passed evidence record* was deposited for it -- see
// `TransitionStateValidationDescriptor`'s own docstring) are the real
// served shape, not invented. The 18 `irc_reverse` geometries the live
// record actually carries are trimmed to 2 here -- the count is not the
// thing under test. `saddle_point` is NOT part of the live shape yet (the
// deployed backend does not serve it) -- tests exercising it below supply
// it explicitly via `overrides`, and the "not served" case is its own
// dedicated test.
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
    // The breadcrumb's "Browse" crumb points at the transition-state
    // browse kind's own canonical path, not `/species?kind=transition_state`.
    it("the breadcrumb starts at the Reactions list", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        await waitFor(() => expect(titleHasKind(document.title, "/transition-state-entries/:ref")).toBe(true))

        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(breadcrumb).getByRole("link", { name: "Reactions" })).toHaveAttribute("href", "/reactions")
    })

    it("leads with the reaction equation as the h1, and never shows a SMILES row", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include")).toEqual(["calculations", "geometries", "review", "trust", "validation_evidence"])
            return HttpResponse.json({ record: mockRecord() })
        }))

        page()
        expect(await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })).toBeVisible()

        // The depositor's own label is not rendered anywhere on the page,
        // not as a pill and not as an identity fact -- house rule:
        // no depositor-typed labels on public pages. `mockRecord()`
        // still deposits `label: "TS0"` on the wire; it must never surface.
        expect(screen.queryByText("Label", { selector: "dt" })).not.toBeInTheDocument()
        expect(screen.queryByText("TS0")).not.toBeInTheDocument()

        // The unmapped SMILES is depositor-typed and unparsed: not shown at all.
        expect(screen.queryByText("Reaction SMILES (unmapped)")).not.toBeInTheDocument()
        expect(document.body.innerHTML).not.toContain("C1=C[C]2C=CCC2C=C1>>")

        // ...and no plain "SMILES" row (the species-entry identity field)
        // is ever rendered for a transition state -- it has no canonical
        // SMILES, and this page must not fabricate an empty one.
        expect(screen.queryByText("SMILES", { selector: "dt" })).not.toBeInTheDocument()
        expect(screen.queryByText("InChIKey")).not.toBeInTheDocument()

        // Neither "no canonical SMILES" sentence is rendered.
        expect(screen.queryAllByText(/no canonical SMILES/i)).toHaveLength(0)

        // Reaction context the API serves.
        expect(screen.getByText("R Addition MultipleBond")).toBeVisible()
        expect(screen.getByRole("link", { name: "Reaction: All versions" }))
            .toHaveAttribute("href", "/reactions/rxn_nu4c52up4c4hqtbtxufwbscq3a")
        // `/reactions/:ref` is a real chooser page now (`ReactionOverviewPage`)
        // -- no "record view not yet available" caveat any more.
        expect(screen.queryByText("(record view not yet available)")).not.toBeInTheDocument()

        // No duplicate "Equation" fact in the Reaction section -- the h1
        // above already IS the rendered equation.
        expect(screen.queryByText("Equation", { selector: "dt" })).not.toBeInTheDocument()

        // Review status -- only the entry's own review-badge pill in the
        // header.
        const header = document.querySelector(".basin-header") as HTMLElement
        expect(within(header).getAllByText("not reviewed")).toHaveLength(1)
        expect(within(header).queryByText("Transition state review")).not.toBeInTheDocument()
    })

    // Mutation check for disclosure
    // adoption (`.disclosure`, never `.geometry-role-
    // disclosure`) and the table primitive (`.data-table`, never
    // `.stage-table`) -- see the identical checks on the other four
    // record pages' own test files.
    it("renders per-role geometry groups as .disclosure and every table as .data-table, never the legacy classes", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        const { container } = page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        expect(container.querySelector(".geometry-role-disclosure")).toBeNull()
        const roleDisclosure = screen.getByText(/IRC reverse/).closest("details") as HTMLElement
        expect(roleDisclosure).not.toBeNull()
        expect(roleDisclosure).toHaveClass("disclosure")

        expect(container.querySelector(".stage-table")).toBeNull()
        const tables = Array.from(container.querySelectorAll("table"))
        expect(tables.length).toBeGreaterThan(0)
        for (const table of tables) {
            expect(table).toHaveClass("data-table")
            expect(table.closest(".table-scroll")).not.toBeNull()
        }
    })

    it("saddle-point: states the imaginary-mode verdict first, under the identity block, when a freq result exists", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    saddle_point: {
                        n_imag: 1,
                        imag_freq_cm1: -768.67,
                        reaction_coordinate_mode_index: null,
                        imaginary_mode_structural_flag: null,
                        calculation_ref: "calc_k4snenouw4m6zbmbzyvqrmn2om",
                        level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" },
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        const statement = document.querySelector(".tse-saddle-point") as HTMLElement
        expect(statement).not.toBeNull()
        expect(statement).toHaveTextContent("1 imaginary mode")
        expect(statement).toHaveTextContent("768.67i")
        expect(statement).not.toHaveTextContent("-768.67")
        expect(statement.querySelector("data")).toHaveAttribute("title", "stored as \u2212768.67")
        expect(statement).toHaveTextContent("b3lyp/def2tzvp")
        expect(within(statement).getByRole("link", { name: "Calculation: Frequency calculation" }))
            .toHaveAttribute("href", "/calculations/calc_k4snenouw4m6zbmbzyvqrmn2om")
    })

    function saddleWith(basis: string | undefined) {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    saddle_point: {
                        ...(basis ? { basis } : {}),
                        n_imag: 1,
                        imag_freq_cm1: -1500,
                        reaction_coordinate_mode_index: null,
                        imaginary_mode_structural_flag: null,
                        calculation_ref: "calc_pinned",
                        level_of_theory: null,
                    },
                }),
            })
        )))
    }

    it("saddle-point: says the result is the declared one when the server reports the declared witness", async () => {
        saddleWith("declared_curvature_witness")
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/the frequency result the declared saddle target pins/)).toBeVisible()
    })

    it.each([["latest_direct_frequency"], [undefined], ["some_future_basis"]])(
        "saddle-point: basis %s describes the result without the declared wording and the page still loads",
        async (basis) => {
            saddleWith(basis)
            page()
            await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
            expect(screen.getByText(/1 imaginary mode/)).toBeVisible()
            expect(screen.queryByText(/declared saddle target pins/)).not.toBeInTheDocument()
        },
    )

    it("saddle-point: flags a structural (higher-order) saddle without citing an internal record number in user-facing text", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    saddle_point: {
                        n_imag: 2,
                        imag_freq_cm1: -50.1,
                        reaction_coordinate_mode_index: 3,
                        imaginary_mode_structural_flag: true,
                        calculation_ref: "calc_structural",
                        level_of_theory: null,
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/Flagged as a higher-order saddle point/)).toBeVisible()
        expect(screen.queryByText(/ADR/)).not.toBeInTheDocument()
    })

    it("saddle-point: names multiple imaginary modes with no designated reaction-coordinate mode distinctly", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    saddle_point: {
                        n_imag: 3,
                        imag_freq_cm1: -50.1,
                        reaction_coordinate_mode_index: null,
                        imaginary_mode_structural_flag: null,
                        calculation_ref: "calc_multi",
                        level_of_theory: null,
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/3 imaginary modes; reaction-coordinate mode not designated/)).toBeVisible()
    })

    it("saddle-point: says plainly when no frequency calculation was deposited at all (has_freq false, saddle_point null)", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    saddle_point: null,
                    evidence_summary: {
                        calculation_count: 3,
                        has_opt: true,
                        has_freq: false,
                        has_sp: true,
                        has_irc: true,
                        has_path_search: false,
                        has_geometry_validation: false,
                        has_scf_stability: false,
                        levels_of_theory: {},
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText("No frequency calculation deposited for this entry.")).toBeVisible()
    })

    // A freq-type calculation CAN be
    // attached (has_freq true, a lit `freq` evidence pill, a `freq` row
    // in the calculation table) while `saddle_point` is still null -- the
    // backend's join finds no `calc_freq_result` row for it. The old,
    // unconditional "No frequency calculation deposited for this entry."
    // was a false claim about deposited data in exactly this case, on
    // every live page (every entry sampled has `has_freq: true`). This
    // pins the corrected, distinct message.
    it("saddle-point: says a freq calc exists with no recorded result when saddle_point is null but has_freq is true", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            // default mockRecord()'s evidence_summary already has has_freq: true
            HttpResponse.json({ record: mockRecord({ saddle_point: null }) })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText("Frequency calculation deposited; no imaginary-mode result recorded.")).toBeVisible()
        expect(screen.queryByText("No frequency calculation deposited for this entry.")).not.toBeInTheDocument()
    })

    // The omitted-field and explicit-null cases
    // are NOT the same claim and must not render the same text. `undefined`
    // means this deployment doesn't serve `saddle_point` at all (a request-
    // side absence); `null` means the backend answered and has nothing to
    // report for this entry (a data-side absence, further split by
    // `has_freq` above). The previous version of this test asserted they
    // rendered identically, which is the wrong behaviour being pinned.
    it("saddle-point: distinguishes the field being unserved (undefined) from an explicit null", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() }) // no `saddle_point` key at all
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText("Saddle-point verdict not served by this deployment.")).toBeVisible()
        expect(screen.queryByText("No frequency calculation deposited for this entry.")).not.toBeInTheDocument()
        expect(screen.queryByText("Frequency calculation deposited; no imaginary-mode result recorded.")).not.toBeInTheDocument()
    })

    it("trust: surfaces the trust label and passed/possible counts beside the review pill", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    trust: {
                        trust_status: "well_supported",
                        evidence: { passed_count: 24, possible_count: 26 },
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/well supported/)).toBeVisible()
        expect(screen.getByText(/24 of 26 checks passed/)).toBeVisible()
    })

    it("made under: the trust badge names its rubric and version, as current", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    trust: {
                        trust_status: "partial",
                        evidence: { passed_count: 10, possible_count: 20, rubric: "computed_transition_state", rubric_version: 2 },
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        const line = document.querySelector('[data-component="made-under"]')
        expect(line).toHaveAttribute("data-state", "current")
        expect(line).toHaveTextContent("Assessed under rubric computed transition state, version 2, the current version.")
        // It sits under the Trust fact it describes, not under the structure assessment beside it.
        const trustFact = screen.getByText("Trust").closest("div") as HTMLElement
        expect(trustFact).toContainElement(line as HTMLElement)
        const structureFact = screen.queryByText("Structure assessment")?.closest("div")
        if (structureFact) expect(structureFact).not.toContainElement(line as HTMLElement)
    })

    it("made under: no line when the server does not name the rubric (an older deployment)", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({ trust: { trust_status: "partial", evidence: { passed_count: 10, possible_count: 20 } } }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(document.querySelector('[data-component="made-under"]')).toBeNull()
    })

    function assessmentWith(structure_assessment: Record<string, unknown> | undefined, checks?: Record<string, string>) {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    trust: {
                        trust_status: "partial",
                        evidence: { passed_count: 10, possible_count: 20, ...(checks ? { checks } : {}) },
                        ...(structure_assessment ? { structure_assessment } : {}),
                    },
                }),
            })
        )))
    }

    // The structure assessment row: the assessment's own answer to a fixed question, in plain words.
    it.each([
        [{ status: "assessed", outcome: "qualified_evidence", declared_targets: 1, contested_target_count: 0 },
            /a declared target has the evidence for a first-order saddle\. Scoped to this question\./],
        [{ status: "assessed", outcome: "no_applicable_candidate", declared_targets: 1 },
            /no declared target qualifies for a first-order saddle — needs investigation/],
        [{ status: "assessed", outcome: "evidence_conflict", declared_targets: 2 },
            /the declared evidence conflicts — needs investigation/],
        [{ status: "assessed", outcome: "qualified_evidence", contested_target_count: 1 },
            /A declared target is contested — needs investigation\./],
        [{ status: "no_declared_target", declared_targets: 0 },
            /Not assessed: no saddle target declared\./],
        [{ status: "not_assessed_over_bound", refusal_code: "structure_selection_evidence_too_large" },
            /Not run: the evidence is over the server's size limits/],
        [{ status: "not_assessed_on_lists" }, /Not assessed on lists\./],
    ])("structure assessment: %j renders its sentence", async (block, sentence) => {
        assessmentWith(block)
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText("Structure assessment")).toBeVisible()
        expect(screen.getByText(sentence)).toBeVisible()
    })

    it("structure assessment: a status token this page was not taught renders as itself and the page still loads", async () => {
        assessmentWith({ status: "some_future_status" })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText("some_future_status")).toBeVisible()
        expect(screen.queryByText(/malformed/i)).not.toBeInTheDocument()
    })

    it("structure assessment: an outcome token this page was not taught renders as itself and the page still loads", async () => {
        assessmentWith({ status: "assessed", outcome: "some_future_outcome" })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/some_future_outcome/)).toBeVisible()
        expect(screen.queryByText(/malformed/i)).not.toBeInTheDocument()
    })

    it("structure assessment: an older deployment that does not serve the block renders no row", async () => {
        assessmentWith(undefined)
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.queryByText("Structure assessment")).not.toBeInTheDocument()
    })

    it.each([
        [{ status: "assessed", outcome: "qualified_evidence", contested_target_count: 1 }],
        [{ status: "assessed", outcome: "no_applicable_candidate" }],
        [{ status: "assessed", outcome: "evidence_conflict" }],
        [{ status: "no_declared_target" }],
        [{ status: "not_assessed_over_bound" }],
        [{ status: "not_assessed_on_lists" }],
    ])("structure assessment: %j beside the declared-target warning claims no disproof and no certificate", async (block) => {
        assessmentWith(block, { declared_saddle_target_qualifies: "warning" })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText("Structure assessment")).toBeVisible() // this block rendered, not a cached one
        expect(screen.getByText(
            "The structure assessment finds no declared saddle target that qualifies, or a declared target is contested — needs investigation",
        )).toBeVisible()
        const text = (document.body.textContent ?? "").toLowerCase()
        expect(text.length).toBeGreaterThan(200)
        for (const banned of ["not a saddle point", "validated", "certified", "certificate", "disproved"]) {
            expect(text).not.toContain(banned)
        }
    })

    function trustWith(checks: Record<string, string> | undefined) {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    trust: { trust_status: "partial", evidence: { passed_count: 10, possible_count: 20, ...(checks ? { checks } : {}) } },
                }),
            })
        )))
    }

    it("trust warnings: each known warning renders its sentence, ending 'needs investigation'", async () => {
        trustWith({
            no_frequency_result_lacks_an_imaginary_mode: "warning",
            no_frequency_result_has_undesignated_imaginary_modes: "warning",
            extra_imaginary_modes_not_flagged: "warning",
            geometry_validation_not_failed_for_source_calculations: "warning",
            reaction_coordinate_designated_for_ts: "missing",
        })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        const list = screen.getByRole("list", { name: "Trust warnings" })
        const items = within(list).getAllByRole("listitem").map((li) => li.textContent)
        expect(items).toEqual([
            "An attached frequency result reports no imaginary mode — needs investigation",
            "An attached frequency result reports several imaginary modes and does not say which is the reaction coordinate — needs investigation",
            "The frequency result carries the recorded structural flag for an extra imaginary mode — needs investigation",
            "An automated geometry check raised a warning — needs investigation",
        ])
    })

    it("trust warnings: an unknown warning key renders as its raw token rather than being dropped", async () => {
        trustWith({ some_future_server_warning: "warning" })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText("some_future_server_warning — needs investigation")).toBeVisible()
    })

    it("trust warnings: an unknown outcome value still renders the page and the known warnings", async () => {
        trustWith({ some_future_check: "needs_review", no_frequency_result_has_undesignated_imaginary_modes: "warning" })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.queryByText(/malformed/i)).not.toBeInTheDocument()
        expect(within(screen.getByRole("list", { name: "Trust warnings" })).getAllByRole("listitem")).toHaveLength(1)
    })

    it("trust warnings: renders no list when nothing is a warning, or when the check map is not served", async () => {
        trustWith({ transition_state_entry_present: "passed", imaginary_frequency_value_present: "not_applicable" })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.queryByRole("list", { name: "Trust warnings" })).not.toBeInTheDocument()
        cleanup()
        trustWith(undefined)
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.queryByRole("list", { name: "Trust warnings" })).not.toBeInTheDocument()
    })

    it("trust warnings and the saddle statement never claim a disproof or a certificate", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    saddle_point: {
                        n_imag: 0,
                        imag_freq_cm1: null,
                        reaction_coordinate_mode_index: null,
                        imaginary_mode_structural_flag: null,
                        calculation_ref: "calc_zero",
                        level_of_theory: null,
                    },
                    trust: {
                        trust_status: "partial",
                        evidence: {
                            passed_count: 10,
                            possible_count: 20,
                            checks: {
                                no_frequency_result_lacks_an_imaginary_mode: "warning",
                                no_frequency_result_has_undesignated_imaginary_modes: "warning",
                            },
                        },
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/0 imaginary modes recorded at this level — needs investigation/)).toBeVisible()
        const text = document.body.textContent ?? ""
        for (const banned of ["not a saddle point", "validated", "certified", "disproved"]) {
            expect(text.toLowerCase()).not.toContain(banned)
        }
    })

    it("evidence pills: positive-only phrasing, present kinds unmuted and absent kinds muted", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        const pills = document.querySelector(".tse-evidence-pills") as HTMLElement
        const opt = within(pills).getByText("opt")
        const pathSearch = within(pills).getByText("path search")
        expect(opt).toHaveClass("value-pill")
        expect(opt).not.toHaveClass("value-pill--muted")
        expect(pathSearch).toHaveClass("value-pill--muted")

        // No "yes"/"no" phrasing anywhere in the pill list.
        expect(within(pills).queryByText(/opt yes/)).not.toBeInTheDocument()

        // The old headline metrics ("Calculation rows N", "Stored
        // geometries N") are gone entirely.
        expect(screen.queryByText("Calculation rows")).not.toBeInTheDocument()
        expect(screen.queryByText("Stored geometries")).not.toBeInTheDocument()
    })

    it("states each stage's level of theory once, in the calculation table, not in a separate by-stage block", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        expect(screen.queryByRole("heading", { name: "Levels of theory by stage" })).not.toBeInTheDocument()

        const calcTable = screen.getByRole("table", { name: "Calculations of this transition state" })
        const optRow = within(calcTable).getByText("opt").closest("tr")
        const ircRow = within(calcTable).getByText("irc").closest("tr")
        expect(optRow).not.toBeNull()
        expect(ircRow).not.toBeNull()
        expect(within(optRow as HTMLElement).getByText("b3lyp/def2tzvp")).toBeVisible()
        expect(within(ircRow as HTMLElement).getByText("b3lyp/def2tzvp")).toBeVisible()
    })

    it("calculation table: orders rows by stage (opt, freq, sp, irc, path_search), not archive order", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: [
                        { calculation_ref: "calc_irc", type: "irc", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: null, software_release: null, workflow_tool_release: null },
                        { calculation_ref: "calc_sp", type: "sp", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: null, software_release: null, workflow_tool_release: null },
                        { calculation_ref: "calc_opt", type: "opt", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: null, software_release: null, workflow_tool_release: null },
                        { calculation_ref: "calc_freq", type: "freq", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: null, software_release: null, workflow_tool_release: null },
                    ],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        const calcTable = screen.getByRole("table", { name: "Calculations of this transition state" })
        const links = within(calcTable).getAllByRole("link")
        expect(links.map((link) => link.textContent)).toEqual(["Optimisation", "Frequency calculation", "Single-point energy", "IRC calculation"])
        expect(links.map((link) => link.getAttribute("href"))).toEqual(["/calculations/calc_opt", "/calculations/calc_freq", "/calculations/calc_sp", "/calculations/calc_irc"])
    })

    it("calculation table: shows a served calculation energy, labeled by kind", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: [{
                        calculation_ref: "calc_energy",
                        type: "sp",
                        quality: "raw",
                        review: { status: "not_reviewed" },
                        level_of_theory: null,
                        software_release: null,
                        workflow_tool_release: null,
                        energy: { energy_hartree: -348.435, energy_kind: "electronic_energy" },
                    }],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/SP electronic energy/)).toBeVisible()
        expect(screen.getByText(/-348.435000 hartree/)).toBeVisible()
    })

    it("calculation table: shows 'version not recorded' for software without a version, rather than silently omitting it", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: [{
                        calculation_ref: "calc_molpro",
                        type: "sp",
                        quality: "raw",
                        review: { status: "not_reviewed" },
                        level_of_theory: null,
                        software_release: { software: "Molpro", version: null },
                        workflow_tool_release: null,
                    }],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/Molpro \(version not recorded\)/)).toBeVisible()
    })

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
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        expect(screen.getByText(/Gaussian 16/)).toBeVisible()
        expect(screen.getByText(/ARC 1\.1\.0/)).toBeVisible()
    })

    it("IRC: says an IRC ran with its point counts and a link, distinct from a passed validation record", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    geometries: [
                        { geometry_ref: "geom_final", input_order: null, output_order: 1, role: "final", natoms: 18, geom_hash: "a" },
                        ...Array.from({ length: 3 }, (_, i) => ({ geometry_ref: `geom_fwd_${i}`, input_order: null, output_order: 2 + i, role: "irc_forward", natoms: 18, geom_hash: `f${i}` })),
                        ...Array.from({ length: 2 }, (_, i) => ({ geometry_ref: `geom_rev_${i}`, input_order: null, output_order: 5 + i, role: "irc_reverse", natoms: 18, geom_hash: `r${i}` })),
                    ],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        const ircSummary = document.querySelector(".tse-irc-summary") as HTMLElement
        expect(within(ircSummary).getByText(/IRC ran/)).toBeVisible()
        expect(ircSummary).toHaveTextContent("3 forward")
        expect(ircSummary).toHaveTextContent("2 reverse")
        expect(within(ircSummary).getByRole("link", { name: "Calculation: IRC calculation" }))
            .toHaveAttribute("href", "/calculations/calc_heds3dt3dsqxmqkcchibvbtwi4")
        expect(within(ircSummary).getByText(/Endpoint identity \(reactant\/product\) not deposited/)).toBeVisible()

        // The old vague "not established" coverage-card wording is gone.
        expect(screen.queryByText("not established")).not.toBeInTheDocument()
    })

    it("IRC: says plainly when no IRC calculation was deposited at all -- a fourth, distinct state", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    evidence_summary: {
                        calculation_count: 2,
                        has_opt: true,
                        has_freq: true,
                        has_sp: false,
                        has_irc: false,
                        has_path_search: false,
                        has_geometry_validation: false,
                        has_scf_stability: false,
                        levels_of_theory: {},
                    },
                    calculations: [],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        const ircSummary = document.querySelector(".tse-irc-summary") as HTMLElement
        expect(within(ircSummary).getByText("No IRC calculation was deposited for this entry.")).toBeVisible()
    })

    it("IRC: renders a passed validation record distinctly from 'ran but no evidence'", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord({ validation: { irc: "present" } }) })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/IRC validation passed/)).toBeVisible()
    })

    it("IRC: renders a failed validation record distinctly", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord({ validation: { irc: "failed" } }) })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/IRC validation failed/)).toBeVisible()
    })

    it("lists calculation and geometry evidence linking to their own record pages", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        expect(document.querySelector('a[href="/calculations/calc_yu6nspewwco74qzh6lwgh6ewxy"]')).not.toBeNull()
        expect(document.querySelector('a[href="/calculations/calc_heds3dt3dsqxmqkcchibvbtwi4"]')).not.toBeNull()
        expect(document.body.textContent).not.toContain("calc_yu6nspewwco74qzh6lwgh6ewxy")

        // The final (saddle-point) geometry is always visible, led with its
        // own label. The single `irc_reverse` point sits behind its
        // direction's disclosure -- named on the closed summary by role,
        // count AND atom count now.
        expect(document.querySelector('a[href="/geometries/geom_mtihqs7ac7btur4efqushl7aoy"]')).not.toBeNull()
        expect(screen.getByText(/^final/)).toBeVisible()
        expect(screen.getByText(/IRC reverse.*1 point.*18 atoms/)).toBeVisible()
    })

    it("keeps the transition-state ref behind a References disclosure, collapsed by default -- with no duplicate reaction link inside it", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        // The reaction-record link now lives ONCE (the Reaction section);
        // the entry's OWN ref is no longer in this disclosure at all (it
        // renders first, inline, in the identity header instead -- see the
        // "own ref" test below) -- so References drops from 4 to 2 rows:
        // TS, reaction entry.
        expect(screen.getByText("ts_uql5lf3xeqnehtostrilmns5yi")).not.toBeVisible()
        await userEvent.setup().click(screen.getByText(bySummaryText(/References \(2\)/)))
        expect(screen.getByText("ts_uql5lf3xeqnehtostrilmns5yi")).toBeVisible()
        // No second "rxn_..." link inside the (now open) disclosure -- only
        // the Reaction section's own link exists anywhere on the page.
        expect(screen.getAllByRole("link", { name: "Reaction: All versions" })).toHaveLength(1)
    })

    it("renders an entry whose owning transition state is withheld (profile=curated): no transition-state ref, one reference row", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord({ transition_state: null }) })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.queryByText("ts_uql5lf3xeqnehtostrilmns5yi")).not.toBeInTheDocument()
        expect(screen.getByText(bySummaryText(/References \(1\)/))).toBeVisible()
    })

    it("shows this entry's OWN ref first in the identity block, at rest, with the data face and a copy button -- and never inside the References disclosure", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        // Visible without opening the (collapsed) References disclosure.
        const facts = screen.getByText("Transition state entry ref").closest(".record-identity-facts") as HTMLElement
        expect(facts).not.toBeNull()
        const ownRefValue = within(facts).getByText(ENTRY_REF)
        expect(ownRefValue).toBeVisible()
        expect(ownRefValue.tagName).toBe("CODE")
        expect(ownRefValue).toHaveClass("data")
        // First fact in the identity block.
        expect(Array.from(facts.children)[0]).toHaveTextContent("Transition state entry ref")
        // Copy button present, named for the field.
        expect(within(facts).getByRole("button", { name: /copy transition state entry ref/i })).toBeVisible()

        // Not duplicated inside References -- only one occurrence of the
        // ref anywhere on the page.
        expect(screen.getAllByText(ENTRY_REF)).toHaveLength(1)
    })

    it("shows not-found style message for an unknown entry ref", async () => {
        server.use(http.get("/api/v1/scientific/transition-state-entries/tse_missing", () => (
            HttpResponse.json({ detail: "not found" }, { status: 404 })
        )))
        page("tse_missing")
        expect(await screen.findByText("Transition state not found")).toBeVisible()
    })

    it("never states the depositor label anywhere, and states the charge/multiplicity fact exactly once", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        // "TS0" appears zero times on the page -- house rule:
        // no depositor-typed labels on public pages at all.
        // `mockRecord()` still deposits `label: "TS0"` on the wire; it must
        // never surface, not in the h1, not as a "Label" identity fact.
        expect(document.body.textContent?.match(/TS0/g)).toBeNull()
        expect(screen.queryByText("Label", { selector: "dt" })).not.toBeInTheDocument()
        expect(screen.getAllByText((_, node) => node?.tagName === "DD" && node.textContent === "0 / doublet (2)"))
            .toHaveLength(1)
    })

    it("leads with the final geometry and collapses each IRC direction behind a disclosure showing its point count and atom count", async () => {
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
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        expect(screen.getByText("Saddle-point geometry")).toBeVisible()
        const geometryLink = (ref: string) => document.querySelector(`a[href="/geometries/${ref}"]`) as HTMLElement
        expect(geometryLink("geom_final")).not.toBeNull()
        expect(geometryLink("geom_final").textContent).toBe("Geometry, 18 atoms")

        expect(screen.getByText(/IRC forward.*3 points.*18 atoms/)).toBeVisible()
        expect(screen.getByText(/IRC reverse.*2 points.*18 atoms/)).toBeVisible()

        expect(geometryLink("geom_fwd_1")).not.toBeVisible()
        expect(geometryLink("geom_rev_1")).not.toBeVisible()

        for (const summary of screen.getAllByText(/IRC (forward|reverse)/)) {
            await userEvent.setup().click(summary)
        }
        expect(geometryLink("geom_fwd_1")).toBeVisible()
        expect(geometryLink("geom_rev_1")).toBeVisible()
    })

    it("teaches geometryRoleSummaryLabel the path_search_point role", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    geometries: [
                        { geometry_ref: "geom_final", input_order: null, output_order: 1, role: "final", natoms: 18, geom_hash: "a" },
                        { geometry_ref: "geom_ps_1", input_order: null, output_order: 2, role: "path_search_point", natoms: 18, geom_hash: "b" },
                    ],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText(/Path search.*1 point/)).toBeVisible()
    })

    it("shows a geometry cited by several calculations once, and counts it once", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    geometries: [
                        { geometry_ref: "geom_shared", input_order: null, output_order: 1, role: "final", natoms: 6, geom_hash: "a" },
                        { geometry_ref: "geom_shared", input_order: null, output_order: 2, role: "final", natoms: 6, geom_hash: "a" },
                        { geometry_ref: "geom_shared", input_order: null, output_order: 3, role: "final", natoms: 6, geom_hash: "a" },
                    ],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(document.querySelectorAll('a[href="/geometries/geom_shared"]')).toHaveLength(1)
    })

    it("says when no saddle-point geometry was deposited, and still lists the other roles", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    geometries: [
                        { geometry_ref: "geom_fwd_1", input_order: null, output_order: 1, role: "irc_forward", natoms: 18, geom_hash: "b" },
                        { geometry_ref: "geom_fwd_2", input_order: null, output_order: 2, role: "irc_forward", natoms: 18, geom_hash: "c" },
                    ],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        expect(screen.getByText("No saddle-point (final) geometry was deposited for this entry.")).toBeVisible()
        expect(screen.queryByText("Saddle-point geometry")).not.toBeInTheDocument()
        expect(screen.getByText(/IRC forward.*2 points/)).toBeVisible()
    })

    it("siblings: renders 'Other saddle points deposited for this reaction' when the search endpoint serves siblings", async () => {
        server.use(
            http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
                HttpResponse.json({ record: mockRecord() })
            )),
            http.get("/api/v1/scientific/transition-states/search", ({ request }) => {
                const url = new URL(request.url)
                expect(url.searchParams.get("reaction_ref")).toBe("rxn_nu4c52up4c4hqtbtxufwbscq3a")
                return HttpResponse.json({
                    records: [
                        {
                            transition_state_entry: { transition_state_entry_ref: ENTRY_REF, created_at: "2026-08-05T00:00:00", review: { status: "not_reviewed" } },
                            transition_state: { label: "TS0" },
                            calculations: [],
                        },
                        {
                            transition_state_entry: { transition_state_entry_ref: "tse_sibling", created_at: "2026-08-02T00:00:00", review: { status: "approved" } },
                            transition_state: { label: "TS1" },
                            calculations: [{
                                calculation_ref: "calc_sib_sp",
                                type: "sp",
                                quality: "raw",
                                review: { status: "not_reviewed" },
                                level_of_theory: { method: "MRCI+Davidson", basis: "aug-cc-pV(T+d)Z", display: "MRCI+Davidson/aug-cc-pV(T+d)Z" },
                                software_release: { software: "Molpro", version: null },
                                workflow_tool_release: null,
                            }],
                        },
                        {
                            // Same label AND same level-of-theory/software as
                            // the row above -- three
                            // indistinguishable "TS4 · ... · NOT REVIEWED"
                            // rows on the hydrazine reaction's own page. The
                            // label is not rendered at all (house
                            // rule); distinguishable by ref
                            // and date instead.
                            transition_state_entry: { transition_state_entry_ref: "tse_sibling_2", created_at: "2026-07-15T00:00:00", review: { status: "not_reviewed" } },
                            transition_state: { label: "TS1" },
                            calculations: [{
                                calculation_ref: "calc_sib2_sp",
                                type: "sp",
                                quality: "raw",
                                review: { status: "not_reviewed" },
                                level_of_theory: { method: "MRCI+Davidson", basis: "aug-cc-pV(T+d)Z", display: "MRCI+Davidson/aug-cc-pV(T+d)Z" },
                                software_release: { software: "Molpro", version: null },
                                workflow_tool_release: null,
                            }],
                        },
                    ],
                })
            }),
        )
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })

        // Excludes itself -- only the other two entries show. Neither
        // depositor label ("TS0"/"TS1") renders anywhere in the section --
        // house rule: no depositor-typed labels on public
        // pages.
        const siblingsSection = await screen.findByText("Other saddle points deposited for this reaction")
        const section = siblingsSection.closest("section") as HTMLElement
        expect(within(section).queryByText("TS0")).not.toBeInTheDocument()
        expect(within(section).queryByText("TS1")).not.toBeInTheDocument()

        // The two same-label, same-lot, same-software siblings are told
        // apart by their position in the list and deposited date.
        const linkSibling1 = within(section).getByRole("link", { name: "Transition state 1" })
        expect(linkSibling1).toHaveAttribute("href", "/transition-state-entries/tse_sibling")
        const linkSibling2 = within(section).getByRole("link", { name: "Transition state 2" })
        expect(linkSibling2).toHaveAttribute("href", "/transition-state-entries/tse_sibling_2")

        expect(within(section).getByText(/deposited 2026-08-02/)).toBeVisible()
        expect(within(section).getByText(/deposited 2026-07-15/)).toBeVisible()

        expect(within(section).getAllByText(/MRCI\+Davidson/)).toHaveLength(2)
        // Each sibling names its program twice: beside the level of theory and in its software cell.
        expect(within(section).getAllByText(/Molpro \(version not recorded\)/)).toHaveLength(4)
    })

    it("siblings: renders a distinct message when the sibling search request fails, instead of silently claiming there are none", async () => {
        server.use(
            http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
                HttpResponse.json({ record: mockRecord() })
            )),
            http.get("/api/v1/scientific/transition-states/search", () => (
                HttpResponse.json({ code: "internal_error", detail: "boom" }, { status: 500 })
            )),
        )
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(await screen.findByText("Could not load sibling saddle points for this reaction.")).toBeVisible()
    })

    it("siblings: omits the section entirely when the reaction has none", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        // defaultSiblingsHandler already serves `{ records: [] }`.
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.queryByText("Other saddle points deposited for this reaction")).not.toBeInTheDocument()
    })

    it("review history: collapses a lone default 'not reviewed / not recorded / not recorded' row to one sentence", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText("Not yet reviewed — no review events recorded.")).toBeVisible()
        expect(screen.queryByRole("table", { name: "Review history" })).not.toBeInTheDocument()
    })

    it("review history: keeps the table when real review events exist", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({
                record: mockRecord({
                    review_history: [
                        { status: "approved", reviewed_at: "2026-08-10T00:00:00", note: "Looks correct." },
                    ],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        const table = screen.getByRole("table", { name: "Review history" })
        expect(within(table).getByText("Looks correct.")).toBeVisible()
        expect(screen.queryByText("Not yet reviewed — no review events recorded.")).not.toBeInTheDocument()
    })
})

// ---------------------------------------------------------------------------
// Orders and picks on this page: the calculation-table
// row order, which calculation a sibling's one-line summary is read from, and
// siblings beyond the first page.
// ---------------------------------------------------------------------------

function siblingCalculation(ref: string, type: string, method: string, softwareName: string) {
    return {
        calculation_ref: ref,
        type,
        quality: "raw",
        review: { status: "not_reviewed" },
        level_of_theory: { method, basis: "def2-tzvp", display: `${method}/def2-tzvp` },
        software_release: { software: softwareName, version: "6.0" },
        workflow_tool_release: null,
    }
}

function sibling(ref: string, calculations: unknown[]) {
    return {
        transition_state_entry: { transition_state_entry_ref: ref, created_at: "2026-08-02T00:00:00", review: { status: "approved" } },
        transition_state: { label: "TS1" },
        calculations,
    }
}

function serveSiblings(records: unknown[], pagination?: { offset: number; limit: number; returned: number; total: number }) {
    server.use(
        http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: mockRecord() })),
        http.get("/api/v1/scientific/transition-states/search", () => HttpResponse.json({
            records,
            ...(pagination ? { pagination } : {}),
            request: { sort: { kind: "display_order", keys: [{ key: "review_rank", direction: "asc", meaning: "review status" }] } },
        })),
    )
}

describe("transition-state entry page: orders and picks that are now stated", () => {
    it("says the calculation table is in stage order, as a display order and not a ranking", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getByText("By stage, then archive order. Not a ranking.")).toBeVisible()
    })

    it("names the stage a sibling's level of theory and software were read from, and reads both from that one calculation", async () => {
        serveSiblings([
            sibling("tse_sib_a", [
                siblingCalculation("calc_a_opt", "opt", "wb97xd", "Gaussian"),
                siblingCalculation("calc_a_sp", "sp", "ccsd(t)-f12", "Molpro"),
            ]),
        ])
        page()
        const section = (await screen.findByText("Other saddle points deposited for this reaction")).closest("section") as HTMLElement
        // The sp stage is the one summarised, and it says so; the opt-stage level of theory is not borrowed.
        expect(within(section).getByText(/sp stage:/)).toBeVisible()
        expect(within(section).getByText(/ccsd\(t\)-f12/i)).toBeVisible()
        expect(within(section).getAllByText(/Molpro 6\.0/).length).toBeGreaterThan(0)
        expect(within(section).queryByText(/wb97xd/i)).not.toBeInTheDocument()
        expect(within(section).queryByText(/Gaussian/)).not.toBeInTheDocument()
    })

    it("falls to the stage the sibling actually has and names that one, not sp", async () => {
        serveSiblings([sibling("tse_sib_b", [siblingCalculation("calc_b_opt", "opt", "wb97xd", "Gaussian")])])
        page()
        const section = (await screen.findByText("Other saddle points deposited for this reaction")).closest("section") as HTMLElement
        expect(within(section).getByText(/opt stage:/)).toBeVisible()
        expect(within(section).queryByText(/sp stage:/)).not.toBeInTheDocument()
    })

    it("does not show one level of theory as the sibling's when its summarised stage has several different ones", async () => {
        serveSiblings([
            sibling("tse_sib_c", [
                siblingCalculation("calc_c_sp1", "sp", "ccsd(t)-f12", "Molpro"),
                siblingCalculation("calc_c_sp2", "sp", "mrci+davidson", "Molpro"),
            ]),
        ])
        page()
        const section = (await screen.findByText("Other saddle points deposited for this reaction")).closest("section") as HTMLElement
        expect(within(section).getByText(/2 calculations at the sp stage differ in level of theory or software/)).toBeVisible()
        expect(within(section).queryByText(/ccsd\(t\)-f12/i)).not.toBeInTheDocument()
        expect(within(section).queryByText(/mrci\+davidson/i)).not.toBeInTheDocument()
    })

    it("states the one-calculation rule once, above the sibling lines", async () => {
        serveSiblings([sibling("tse_sib_d", [siblingCalculation("calc_d_sp", "sp", "ccsd(t)-f12", "Molpro")])])
        page()
        const section = (await screen.findByText("Other saddle points deposited for this reaction")).closest("section") as HTMLElement
        expect(within(section).getByText(/read from one calculation of that saddle point/)).toBeVisible()
        expect(within(section).getByText(/This entry is left out of the list below; a count above includes it unless it is hidden by default/)).toBeVisible()
    })

    it("pages the siblings: more than one page says so, and Next asks for the following window", async () => {
        const user = userEvent.setup()
        const seen: string[] = []
        server.use(
            http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: mockRecord() })),
            http.get("/api/v1/scientific/transition-states/search", ({ request }) => {
                const params = new URL(request.url).searchParams
                const offset = Number(params.get("offset") ?? 0)
                const limit = Number(params.get("limit") ?? 2)
                seen.push(`${offset}:${limit}`)
                const all = Array.from({ length: 5 }, (_, index) => sibling(`tse_many_${index + 1}`, []))
                const records = all.slice(offset, offset + limit)
                return HttpResponse.json({ records, pagination: { offset, limit, returned: records.length, total: all.length } })
            }),
        )
        page()
        const section = (await screen.findByText("Other saddle points deposited for this reaction")).closest("section") as HTMLElement
        expect(await within(section).findByText("Showing 1–2 of 5 saddle points.")).toBeVisible()
        expect(within(section).getByRole("link", { name: "Transition state 1" })).toHaveAttribute("href", "/transition-state-entries/tse_many_1")
        await user.click(within(section).getByRole("button", { name: "Next" }))
        expect(await within(section).findByText("Showing 3–4 of 5 saddle points.")).toBeVisible()
        expect(seen).toContain("2:2")
        expect(within(section).getByRole("link", { name: "Transition state 3" })).toHaveAttribute("href", "/transition-state-entries/tse_many_3")
        expect(within(section).queryByRole("link", { name: "Transition state 1" })).not.toBeInTheDocument()
    })
})

describe("transition-state entry page: siblings when this entry is the only record on a page", () => {
    it("keeps the section and its pager when the page holds only this entry, so the reader can go back", async () => {
        const user = userEvent.setup()
        const seen: string[] = []
        server.use(
            http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: mockRecord() })),
            http.get("/api/v1/scientific/transition-states/search", ({ request }) => {
                const params = new URL(request.url).searchParams
                const offset = Number(params.get("offset") ?? 0)
                const limit = Number(params.get("limit") ?? 50)
                seen.push(`${offset}:${limit}`)
                const all = Array.from({ length: 51 }, (_, index) => (
                    index === 50
                        ? { transition_state_entry: { transition_state_entry_ref: ENTRY_REF, created_at: "2026-08-05T00:00:00", review: { status: "not_reviewed" } }, transition_state: { label: "TS0" }, calculations: [] }
                        : sibling(`tse_p_${index + 1}`, [])
                ))
                const records = all.slice(offset, offset + limit)
                return HttpResponse.json({ records, pagination: { offset, limit, returned: records.length, total: all.length } })
            }),
        )
        page()
        const heading = await screen.findByText("Other saddle points deposited for this reaction")
        expect(await within(heading.closest("section") as HTMLElement).findByText("Showing 1\u201350 of 51 saddle points.")).toBeVisible()
        await user.click(screen.getByRole("button", { name: "Next" }))
        expect(await screen.findByText("Showing 51\u201351 of 51 saddle points.")).toBeVisible()
        expect(screen.getByRole("heading", { name: "Other saddle points deposited for this reaction" })).toBeVisible()
        expect(screen.getByText("No other saddle points are on this page.")).toBeVisible()
        await user.click(screen.getByRole("button", { name: "Previous" }))
        expect(await screen.findByRole("link", { name: "Transition state 1" })).toHaveAttribute("href", "/transition-state-entries/tse_p_1")
        expect(seen).toContain("50:50")
    })

    it("a page that is only this entry, with no other page, shows no section", async () => {
        server.use(
            http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: mockRecord() })),
            http.get("/api/v1/scientific/transition-states/search", () => HttpResponse.json({
                records: [{ transition_state_entry: { transition_state_entry_ref: ENTRY_REF, created_at: "2026-08-05T00:00:00", review: { status: "not_reviewed" } }, transition_state: { label: "TS0" }, calculations: [] }],
                pagination: { offset: 0, limit: 50, returned: 1, total: 1 },
            })),
        )
        page()
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.queryByText("Other saddle points deposited for this reaction")).not.toBeInTheDocument()
    })

    it("returns to the first page when the entry shown changes (the page position is not carried to another entry)", async () => {
        const user = userEvent.setup()
        const seen: string[] = []
        server.use(
            http.get("/api/v1/scientific/transition-state-entries/:ref", ({ params }) => HttpResponse.json({ record: mockRecord({ transition_state_entry: { ...mockRecord().transition_state_entry, transition_state_entry_ref: String(params.ref) } }) })),
            http.get("/api/v1/scientific/transition-states/search", ({ request }) => {
                const params = new URL(request.url).searchParams
                const offset = Number(params.get("offset") ?? 0)
                const limit = Number(params.get("limit") ?? 50)
                seen.push(`${offset}`)
                const all = Array.from({ length: 120 }, (_, index) => sibling(`tse_q_${index + 1}`, []))
                const records = all.slice(offset, offset + limit)
                return HttpResponse.json({ records, pagination: { offset, limit, returned: records.length, total: all.length } })
            }),
        )
        render(
            <MemoryRouter initialEntries={[`/transition-state-entries/${ENTRY_REF}`]}>
                <Link to="/transition-state-entries/tse_other">go elsewhere</Link>
                <Routes><Route path="/transition-state-entries/:entryRef" element={<TransitionStateEntryPage />} /></Routes>
            </MemoryRouter>,
        )
        await screen.findByText("Showing 1–50 of 120 saddle points.")
        await user.click(screen.getByRole("button", { name: "Next" }))
        await screen.findByText("Showing 51–100 of 120 saddle points.")
        await user.click(screen.getByRole("link", { name: "go elsewhere" }))
        expect(await screen.findByText("Showing 1–50 of 120 saddle points.")).toBeVisible()
        expect(seen.at(-1)).toBe("0")
    })
})

describe("TransitionStateEntryPage -- no depositor-typed text", () => {
    it("prints none of the fields a depositor typed, in text or in any attribute", async () => {
        const planted = plantSentinels(mockRecord(), ["label", "note", "unmapped_smiles"])
        expectPlanted(planted)
        const { value } = planted
        // A string that is not SMILES but is shaped like it, in the one field the page does not print.
        ;(value.transition_state_entry as { unmapped_smiles: string }).unmapped_smiles = ADVERSARIAL.unmappedSmiles
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: value })))
        page()
        // The fixture reached the page: the reaction equation heading and the entry ref rendered.
        await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        expect(screen.getAllByText(ENTRY_REF).length).toBeGreaterThan(0)

        expectDepositorTextOnlyInNotes()
        expectNoAdversarialText()
    })
})

const DEFINITION = "each molecule's lowest computed energy at this level, from this program"

/** Page text with only the two exact served sentences that name a ranking word to deny it removed. */
function withoutServedWording(text: string): string {
    return text.replace(DEFINITION, "").replace(CALCULATION_ROW_ORDER_NOTE, "")
}

describe("TransitionStateEntryPage -- the redesigned page", () => {
    function withRecord(overrides: Record<string, unknown> = {}) {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: mockRecord(overrides) })))
    }
    const EVIDENCE = [
        { kind: "imaginary_mode", passed: true, rationale: "r", reconstruction_calculation_ref: "calc_freq_ev" },
        { kind: "irc", passed: true, rationale: "r", reconstruction_calculation_ref: "calc_irc_ev" },
        {
            kind: "energy_ordering", passed: false, rationale: "r",
            compared_energies: [
                { participant: "ts", energy_kind: "electronic", energy_hartree: -1, source_calculation_ref: "calc_e_ts" },
                { participant: "reactant:1", energy_kind: "electronic", energy_hartree: -2, source_calculation_ref: "calc_e_r0" },
                { participant: "product:2", energy_kind: "electronic", energy_hartree: -3, source_calculation_ref: "calc_e_p2" },
            ],
        },
    ]
    const SADDLE = {
        n_imag: 1,
        imag_freq_cm1: -768.67,
        reaction_coordinate_mode_index: 1,
        imaginary_mode_structural_flag: false,
        calculation_ref: "calc_freq1",
        level_of_theory: { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" },
    }

    it("names the page from the reaction's codes: the h1 is a sentence, never a ref, and the tab title follows", async () => {
        withRecord()
        page()
        const heading = await screen.findByRole("heading", { level: 1, name: /^Transition state of/ })
        await waitFor(() => expect(heading).toHaveTextContent(/^Transition state of CH4/))
        expect(heading.textContent).not.toMatch(/\b(tse|rxe|rxn|ts)_[a-z0-9]+/)
        expect(heading.textContent).not.toContain("TS0")
        await waitFor(() => expect(document.title).toBe("CH4 + OH → CH3 + H2O · Transition state · TCKDB"))
        expect(titleHasKind(document.title, "/transition-state-entries/:ref")).toBe(true)
    })

    it("falls back to a plain h1 when the reaction entry cannot be read", async () => {
        withRecord()
        server.use(http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/full`, () => HttpResponse.json({ detail: "x" }, { status: 500 })))
        page()
        expect(await screen.findByRole("heading", { level: 1, name: "Transition state of this reaction" })).toBeVisible()
        expect(document.title).toBe("Transition state · TCKDB")
    })

    it("breadcrumbs run Reactions, the reaction, this deposited version, this transition state", async () => {
        withRecord()
        page()
        const nav = await screen.findByRole("navigation", { name: "Breadcrumb" })
        expect(within(nav).getByRole("link", { name: "Reactions" })).toHaveAttribute("href", "/reactions")
        expect(await within(nav).findByRole("link", { name: "Version 2" })).toHaveAttribute("href", `/reaction-entries/${REACTION_ENTRY_REF}`)
        expect(within(nav).getByText("This transition state")).toHaveAttribute("aria-current", "page")
        await waitFor(() => expect(within(nav).getAllByRole("link")[1]).toHaveAttribute("href", "/reactions/rxn_nu4c52up4c4hqtbtxufwbscq3a"))
    })

    it("draws the energy hill from this entry's own profile only, with its peak marked", async () => {
        withRecord()
        page()
        const figure = await screen.findByRole("group", { name: /Energy relative to the reactants/ })
        expect(figure).toHaveTextContent("+84.3 kJ/mol")
        expect(figure).not.toHaveTextContent("12.5")
        expect(figure).toHaveTextContent("This transition state")
        expect(document.querySelector(".energy-profile-peak")).not.toBeNull()
        const table = screen.getByRole("table", { name: /Every point of the selected profile/ })
        expect(table).not.toHaveTextContent("tse_sibling")
        expect(screen.queryByLabelText("Profile")).toBeNull()
    })

    it("says why there is no hill when this entry has no complete profile, from the read's own reason", async () => {
        withRecord()
        server.use(http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/energy-profile`, () => HttpResponse.json(energyRead({
            profiles: [profileFor("tse_sibling", 12.5)],
            unavailable: [
                { transition_state_entry_ref: ENTRY_REF, transition_state_entry_review_status: "not_reviewed", code: "no_product_energy_at_level", level_of_theory: LEVEL, program: "Gaussian" },
                { transition_state_entry_ref: "tse_sibling", transition_state_entry_review_status: "not_reviewed", code: "no_ts_energy" },
            ],
        }))))
        page()
        expect(await screen.findByText("No complete energy profile is available for this transition state.")).toBeVisible()
        expect(screen.queryByText(/at some levels/)).toBeNull()
        const list = screen.getByRole("heading", { name: "Why there is no profile" }).parentElement!
        expect(list).toHaveTextContent("wb97xd/def2tzvp")
        expect(list).toHaveTextContent("Not every product has an energy")
        expect(list).not.toHaveTextContent(ENTRY_REF)
        expect(list).not.toHaveTextContent("not reviewed")
        expect(list).not.toHaveTextContent("tse_sibling")
        expect(screen.queryByRole("group", { name: /Energy relative to the reactants/ })).toBeNull()
    })

    it("says so when the read lists nothing about this entry", async () => {
        withRecord()
        server.use(http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/energy-profile`, () => HttpResponse.json(energyRead({ profiles: [], unavailable: [] }))))
        page()
        expect(await screen.findByText("No complete energy profile is available for this transition state.")).toBeVisible()
        expect(screen.getByText(/gives no reason for this transition state/)).toBeVisible()
    })

    it("glosses the imaginary frequency with the term, and links its value to the frequency calculation", async () => {
        withRecord({ saddle_point: SADDLE })
        page()
        const term = await screen.findByRole("button", { name: "imaginary frequency" })
        expect(term).toHaveAttribute("aria-describedby")
        expect(document.getElementById(term.getAttribute("aria-describedby")!)).toHaveTextContent("The one motion that carries a transition state downhill")
        const value = screen.getByRole("link", { name: /768\.67/ })
        expect(value).toHaveAttribute("href", "/calculations/calc_freq1")
        expect(value).toHaveTextContent("cm⁻¹")
    })

    it("checks table: each row says Recorded or Not recorded, with the outcome in words and an evidence link where one exists", async () => {
        withRecord({
            saddle_point: SADDLE,
            validation: { irc: "present", imaginary_mode: "present", energy_ordering: "failed" },
            validation_evidence: EVIDENCE,
        })
        page()
        const region = await screen.findByRole("region", { name: "Checks on this transition state" })
        const rows = within(region).getAllByRole("row").slice(1)
        expect(rows).toHaveLength(3)
        expect(rows[0]).toHaveTextContent("One imaginary frequency")
        expect(rows[0]).toHaveTextContent("Recorded")
        expect(rows[0]).toHaveTextContent("Passed")
        expect(within(rows[0]).getByRole("link", { name: "Frequency calculation" })).toHaveAttribute("href", "/calculations/calc_freq_ev")
        expect(rows[1]).toHaveTextContent("IRC connects reactants and products")
        expect(rows[1]).toHaveTextContent("Passed")
        expect(within(rows[1]).getByRole("link", { name: "IRC calculation" })).toHaveAttribute("href", "/calculations/calc_irc_ev")
        expect(rows[2]).toHaveTextContent("Energy ordering (transition state above both sides)")
        expect(rows[2]).toHaveTextContent("Did not pass")
        expect(within(rows[2]).getByRole("link", { name: "Energy of transition state" })).toHaveAttribute("href", "/calculations/calc_e_ts")
        expect(within(rows[2]).getByRole("link", { name: "Energy of reactant 1" })).toHaveAttribute("href", "/calculations/calc_e_r0")
        expect(within(rows[2]).getByRole("link", { name: "Energy of product 2" })).toHaveAttribute("href", "/calculations/calc_e_p2")
        expect(within(rows[2]).getAllByRole("link")).toHaveLength(3)
    })

    it("checks table: links of one kind carry their calculation ref so their names differ", async () => {
        withRecord({
            validation: { irc: "present", imaginary_mode: "absent", energy_ordering: "absent" },
            validation_evidence: [
                { kind: "irc", passed: true, rationale: "r", reconstruction_calculation_ref: "calc_irc_a" },
                { kind: "irc", passed: true, rationale: "r", reconstruction_calculation_ref: "calc_irc_b" },
            ],
        })
        page()
        const region = await screen.findByRole("region", { name: "Checks on this transition state" })
        const row = within(region).getAllByRole("row")[2]
        expect(within(row).getByRole("link", { name: "IRC calculation calc_irc_a" })).toHaveAttribute("href", "/calculations/calc_irc_a")
        expect(within(row).getByRole("link", { name: "IRC calculation calc_irc_b" })).toHaveAttribute("href", "/calculations/calc_irc_b")
    })

    it("checks table: a token the response leaves out says the deployment does not serve it", async () => {
        const record = mockRecord()
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: { ...record, validation: { irc: "absent" } } })))
        page()
        const region = await screen.findByRole("region", { name: "Checks on this transition state" })
        const rows = within(region).getAllByRole("row").slice(1)
        expect(rows[0]).toHaveTextContent("Not served by this deployment")
        expect(rows[2]).toHaveTextContent("Not served by this deployment")
        expect(rows[1]).toHaveTextContent("No check of this kind was deposited")
        expect(rows[0]).not.toHaveTextContent("No check of this kind was deposited")
    })

    it("checks table: a check recorded without a calculation to name shows no link", async () => {
        withRecord({
            validation: { irc: "present", imaginary_mode: "absent", energy_ordering: "absent" },
            validation_evidence: [{ kind: "irc", passed: true, rationale: "r", reconstruction_calculation_ref: null }],
        })
        page()
        const region = await screen.findByRole("region", { name: "Checks on this transition state" })
        const rows = within(region).getAllByRole("row").slice(1)
        expect(rows[1]).toHaveTextContent("Recorded")
        expect(within(rows[1]).queryByRole("link")).toBeNull()
        expect(rows[1]).toHaveTextContent("none to link")
    })

    it("checks table: a token this page was not taught shows as code with its own sentence, and links nothing", async () => {
        withRecord({
            validation: { irc: "mystery_irc", imaginary_mode: "other_token", energy_ordering: "absent" },
            validation_evidence: EVIDENCE,
        })
        page()
        const region = await screen.findByRole("region", { name: "Checks on this transition state" })
        const rows = within(region).getAllByRole("row").slice(1)
        expect(rows[0]).toHaveTextContent("Not known")
        expect(rows[0]).not.toHaveTextContent("Not read")
        expect(rows[0].querySelector("code")).toHaveTextContent("other_token")
        expect(rows[0]).toHaveTextContent("outcome not known to this page")
        expect(rows[1].querySelector("code")).toHaveTextContent("mystery_irc")
        expect(rows[1]).toHaveTextContent("outcome not known to this page")
        for (const row of rows.slice(0, 2)) {
            expect(row).not.toHaveTextContent("Passed")
            expect(row).not.toHaveTextContent("Not recorded")
            expect(within(row).queryByRole("link")).toBeNull()
        }
        expect(rows[2]).toHaveTextContent("Not recorded")
    })

    it("IRC card: a token this page was not taught shows as code with its own sentence, not as a missing IRC", async () => {
        withRecord({ validation: { irc: "mystery_irc" } })
        page()
        const irc = await screen.findByRole("region", { name: "IRC" })
        expect(irc.querySelector("code")).toHaveTextContent("mystery_irc")
        expect(irc).toHaveTextContent("outcome not known to this page")
        expect(irc).not.toHaveTextContent("IRC ran")
        expect(irc).not.toHaveTextContent("No IRC calculation")
    })

    it.each([[-1234.5678], [1234.5678]])("imaginary frequency: stored %s shows the magnitude with an i and keeps the stored value on hover", async (stored) => {
        withRecord({ saddle_point: { ...SADDLE, imag_freq_cm1: stored } })
        page()
        const value = await screen.findByRole("link", { name: /1234\.57i/ })
        expect(value).toHaveTextContent("1234.57i cm⁻¹")
        const sign = stored < 0 ? "\u2212" : ""
        expect(value.querySelector("data")).toHaveAttribute("title", `stored as ${sign}1234.5678`)
    })

    it("trust card: states the status and how many checks passed", async () => {
        withRecord({ trust: { trust_status: "well_supported", evidence: { passed_count: 5, possible_count: 6, checks: {} } } })
        page()
        const trust = await screen.findByRole("region", { name: "Trust" })
        expect(trust).toHaveTextContent("well supported")
        expect(trust).toHaveTextContent("5 of 6 checks passed")
    })

    it("hill: says its comparison is TCKDB's own, and does not name this entry's ref or review on the hill's facts", async () => {
        withRecord()
        page()
        await screen.findByRole("group", { name: /Energy relative to the reactants/ })
        expect(screen.getByText("TCKDB's comparison at one level and program; the depositor's check may use other energies.")).toBeVisible()
        expect(screen.queryByText("Transition-state entry", { selector: "dt" })).toBeNull()
    })

    it("hill: with two profiles for this entry, offers a picker with an order note that fits one entry", async () => {
        withRecord()
        const second = { ...profileFor(ENTRY_REF, 40), level_of_theory: { ...LEVEL, level_of_theory_ref: "lot_b", method: "b3lyp", display: "b3lyp/def2tzvp" }, program: "Orca" }
        server.use(http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/energy-profile`, () => HttpResponse.json(energyRead({ profiles: [profileFor(ENTRY_REF, 84.3), second, profileFor("tse_sibling", 12.5)] }))))
        const user = userEvent.setup()
        page()
        const picker = await screen.findByLabelText("Profile")
        expect(within(picker).getAllByRole("option")).toHaveLength(2)
        expect(screen.getByText(/By level, then program\./)).toBeVisible()
        expect(screen.queryByText(/approved first/)).toBeNull()
        await user.selectOptions(picker, "1")
        expect(screen.getByRole("group", { name: /Energy relative to the reactants/ })).toHaveTextContent("+40.0 kJ/mol")
    })

    it("hill: says what the default view left out is for the reaction entry", async () => {
        withRecord()
        server.use(http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/energy-profile`, () => HttpResponse.json(energyRead({
            request: { hidden_by_default: null, hidden_by_default_by_scope: { transition_state_entries: { rejected: 2 } } },
        }))))
        page()
        expect(await screen.findByText("For this reaction entry: Not listed by default: 2 rejected transition-state entries.")).toBeVisible()
    })

    function citedRead() {
        const cited = (ref: string, tse: string) => ({ role: "ts_energy", calculation_ref: ref, review_status: "not_reviewed", transition_state_entry_ref: tse, electronic_energy_hartree: -115.2, energy_type: "single_point" })
        return energyRead({
            cited_by_kinetics: [
                { kinetics_ref: "kin_mine", calculations: [cited("calc_cited_mine", ENTRY_REF), cited("calc_cited_other", "tse_sibling")] },
                { kinetics_ref: "kin_other", calculations: [cited("calc_cited_other2", "tse_sibling")] },
            ],
        })
    }

    it("cited energies: keeps those that cite this entry and drops the rest, and the records with none left", async () => {
        withRecord()
        server.use(http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/energy-profile`, () => HttpResponse.json(citedRead())))
        page()
        const table = await screen.findByRole("table", { name: /Calculations cited by each record/ })
        expect(within(table).getByRole("link", { name: /115\.2/ })).toHaveAttribute("href", "/calculations/calc_cited_mine")
        expect(within(table).getAllByRole("row")).toHaveLength(2)
        expect(table).toHaveTextContent("Record 1")
        expect(table).not.toHaveTextContent("Record 2")
        expect(table).not.toHaveTextContent("Rate record")
        expect(table).not.toHaveTextContent(/kin_/)
    })

    it("cited energies: no table when none cites this entry", async () => {
        withRecord()
        const read = citedRead()
        read.cited_by_kinetics = read.cited_by_kinetics.slice(1)
        server.use(http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/energy-profile`, () => HttpResponse.json(read)))
        page()
        await screen.findByRole("group", { name: /Energy relative to the reactants/ })
        expect(screen.queryByRole("table", { name: /Calculations cited by each record/ })).toBeNull()
    })

    it("draws the picture without reading the entry a second time", async () => {
        const searches: string[] = []
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, ({ request }) => {
            searches.push(new URL(request.url).search)
            return HttpResponse.json({ record: mockRecord() })
        }))
        page()
        await screen.findByRole("img", { name: /Ball-and-stick picture of transition state/ })
        expect(searches).toHaveLength(1)
        expect(searches[0]).not.toBe("?include=geometries")
    })

    it("checks table: a check with no deposited record is Not recorded, and a reported frequency count is not turned into a result", async () => {
        withRecord({ saddle_point: SADDLE, validation: { irc: "absent" } })
        page()
        const region = await screen.findByRole("region", { name: "Checks on this transition state" })
        const rows = within(region).getAllByRole("row").slice(1)
        for (const row of rows) {
            expect(row).toHaveTextContent("Not recorded")
            expect(row).not.toHaveTextContent("Passed")
        }
        expect(rows[0]).toHaveTextContent("reports 1 imaginary frequency. That is a result, not a recorded check.")
        expect(rows[2]).toHaveTextContent("none to link")
        for (const row of rows) expect(within(row).queryByRole("link")).toBeNull()
        expect(screen.getByText(/Not recorded means not deposited, not failed\./)).toBeVisible()
    })

    it("shows trust and IRC side by side", async () => {
        withRecord({
            trust: { trust_status: "well_supported", evidence: { passed_count: 5, possible_count: 6, checks: {} } },
        })
        page()
        const trust = await screen.findByRole("region", { name: "Trust" })
        const irc = screen.getByRole("region", { name: "IRC" })
        expect(trust.parentElement).toBe(irc.parentElement)
        expect(trust).toHaveTextContent("well supported")
        expect(trust).toHaveTextContent("5 of 6 checks passed")
        expect(irc).toHaveTextContent("IRC ran")
    })

    it("draws the picture of the saddle point from its final geometry", async () => {
        withRecord()
        page()
        expect(await screen.findByRole("img", { name: /Ball-and-stick picture of transition state/ })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Show in 3D" })).toBeInTheDocument()
    })

    it("keyboard: Tab reaches the imaginary-frequency term, Esc closes it, then each check's evidence link", async () => {
        const user = userEvent.setup()
        withRecord({ saddle_point: SADDLE, validation: { irc: "present", imaginary_mode: "present", energy_ordering: "present" }, validation_evidence: EVIDENCE })
        page()
        const term = await screen.findByRole("button", { name: "imaginary frequency" })
        const popover = document.getElementById(term.getAttribute("aria-describedby")!)!
        for (let step = 0; step < 80 && document.activeElement !== term; step++) await user.tab()
        expect(document.activeElement).toBe(term)
        expect(popover).toHaveAttribute("data-open", "true")
        await user.keyboard("{Escape}")
        expect(popover).toHaveAttribute("data-open", "false")
        expect(document.activeElement).toBe(term)
        const region = screen.getByRole("region", { name: "Checks on this transition state" })
        for (const name of ["Frequency calculation", "IRC calculation", "Energy of transition state", "Energy of reactant 1"]) {
            const link = within(region).getByRole("link", { name })
            for (let step = 0; step < 80 && document.activeElement !== link; step++) await user.tab()
            expect(document.activeElement).toBe(link)
        }
    })

    it("uses no ranking word and no literal double hyphen anywhere on the page", async () => {
        withRecord({ saddle_point: SADDLE, validation: { irc: "present", imaginary_mode: "present", energy_ordering: "present" } })
        page()
        await screen.findByRole("group", { name: /Energy relative to the reactants/ })
        await screen.findByRole("region", { name: "Checks on this transition state" })
        expect(rankingWords(withoutServedWording(textOf(document.body)))).toEqual([])
        expect(textOf(document.body)).not.toContain("--")
    })

    it("the ranking guard still fails when the served definition gains a ranking claim", async () => {
        withRecord()
        server.use(http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY_REF}/energy-profile`, () => HttpResponse.json(energyRead({
            profiles: [{ ...profileFor(ENTRY_REF, 84.3), definition: `${DEFINITION} This is the best profile.` }],
        }))))
        page()
        await screen.findByRole("group", { name: /Energy relative to the reactants/ })
        expect(rankingWords(withoutServedWording(textOf(document.body)))).toEqual(["best"])
    })

    it("never reads values from a sibling entry: siblings are links, and a sibling's saddle point is not shown as this one's", async () => {
        withRecord()
        server.use(http.get("/api/v1/scientific/transition-states/search", () => HttpResponse.json({
            records: [{
                transition_state_entry: { transition_state_entry_ref: "tse_sibling", created_at: "2026-08-06T00:00:00Z", review: { status: "approved" } },
                transition_state: { label: "TS9" },
                calculations: [],
            }],
        })))
        page()
        const sibling = await screen.findByRole("link", { name: "Transition state 1" })
        expect(sibling).toHaveAttribute("href", "/transition-state-entries/tse_sibling")
        expect(screen.queryByText("TS9")).toBeNull()
    })
})

describe("TransitionStateEntryPage -- depositor text only in a note", () => {
    it("shows the transition state's note inside a depositor note and its label nowhere", async () => {
        const planted = plantSentinels(mockRecord(), ["label", "note", "unmapped_smiles"])
        expectPlanted(planted)
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: planted.value })))
        page()
        const note = await screen.findByLabelText("Note from the depositor")
        expect(note).toHaveTextContent(planted.byKey.note[0])
        expectDepositorTextOnlyInNotes()
        expectNoAdversarialText()
    })
})
