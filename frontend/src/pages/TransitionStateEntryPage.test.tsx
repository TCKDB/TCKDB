import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import TransitionStateEntryPage from "./TransitionStateEntryPage"
import { bySummaryText } from "../test/disclosureQueries"

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

const server = setupServer(defaultSiblingsHandler)
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
// response (curled 2026-09-03) -- field names, nesting and the `has_irc:
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
    // browse kind's own canonical path now (per-kind browse paths change),
    // not the legacy `/species?kind=transition_state` it used to.
    it("the breadcrumb's Browse crumb links to /transition-states", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "C1=C[C]2C=CCC2C=C1 <=> C1=Cc2ccccc2C1 + [H]" })

        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(breadcrumb).getByRole("link", { name: "Browse" })).toHaveAttribute("href", "/transition-states")
    })

    it("leads with the reaction equation as the h1, and never shows a SMILES row", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include")).toEqual(["calculations", "geometries", "review", "trust"])
            return HttpResponse.json({ record: mockRecord() })
        }))

        page()
        expect(await screen.findByRole("heading", { name: "C1=C[C]2C=CCC2C=C1 <=> C1=Cc2ccccc2C1 + [H]" })).toBeVisible()

        // The depositor's own label is not rendered anywhere on the page,
        // not as a pill and not as an identity fact -- house rule widened
        // 2026-09: no depositor-typed labels on public pages. `mockRecord()`
        // still deposits `label: "TS0"` on the wire; it must never surface.
        expect(screen.queryByText("Label", { selector: "dt" })).not.toBeInTheDocument()
        expect(screen.queryByText("TS0")).not.toBeInTheDocument()

        // The unmapped SMILES the API actually serves is shown, relabeled...
        const unmappedRow = screen.getByText("Reaction SMILES (unmapped)").closest("div")
        expect(unmappedRow).not.toBeNull()
        expect(within(unmappedRow as HTMLElement).getByText("C1=C[C]2C=CCC2C=C1>>C1=CC2=C(C=C1)CC=C2.[H]")).toBeVisible()

        // ...and no plain "SMILES" row (the species-entry identity field)
        // is ever rendered for a transition state -- it has no canonical
        // SMILES, and this page must not fabricate an empty one.
        expect(screen.queryByText("SMILES", { selector: "dt" })).not.toBeInTheDocument()
        expect(screen.queryByText("InChIKey")).not.toBeInTheDocument()

        // Neither duplicate "no canonical SMILES" sentence is rendered --
        // only the Reaction section's own lede survives.
        expect(screen.queryAllByText(/no canonical SMILES/i)).toHaveLength(0)
        expect(screen.getByText(/A transition state is identified by the reaction it connects/)).toBeVisible()

        // Reaction context the API serves.
        expect(screen.getByText("R Addition MultipleBond")).toBeVisible()
        expect(screen.getByRole("link", { name: "rxn_nu4c52up4c4hqtbtxufwbscq3a" }))
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

    // Item 5/7, design/foundations PR B: mutation check for disclosure
    // adoption (`.disclosure`, never the retired `.geometry-role-
    // disclosure`) and the table primitive (`.data-table`, never
    // `.stage-table`) -- see the identical checks on the other four
    // record pages' own test files.
    it("renders per-role geometry groups as .disclosure and every table as .data-table, never the retired classes", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        const { container } = page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

        const statement = document.querySelector(".tse-saddle-point") as HTMLElement
        expect(statement).not.toBeNull()
        expect(statement).toHaveTextContent("1 imaginary mode")
        expect(statement).toHaveTextContent("-768.7")
        expect(statement).toHaveTextContent("b3lyp/def2tzvp")
        expect(within(statement).getByRole("link", { name: "calc_k4snenouw4m6zbmbzyvqrmn2om" }))
            .toHaveAttribute("href", "/calculations/calc_k4snenouw4m6zbmbzyvqrmn2om")

        // It is the first thing under the identity header -- immediately
        // adjacent in the DOM, not buried further down the page.
        const identityHeader = document.querySelector(".record-identity-header") as HTMLElement
        expect(identityHeader.nextElementSibling).toBe(statement)
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText(/the frequency result the declared saddle target pins/)).toBeVisible()
    })

    it.each([["latest_direct_frequency"], [undefined], ["some_future_basis"]])(
        "saddle-point: basis %s describes the result without the declared wording and the page still loads",
        async (basis) => {
            saddleWith(basis)
            page()
            await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
            expect(screen.getByText(/1 imaginary mode/)).toBeVisible()
            expect(screen.queryByText(/declared saddle target pins/)).not.toBeInTheDocument()
        },
    )

    it("saddle-point: flags a structural (higher-order) saddle without citing an internal ADR number in user-facing text", async () => {
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText(/flagged as a higher-order saddle point/)).toBeVisible()
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText("No frequency calculation deposited for this entry.")).toBeVisible()
    })

    // BLOCKING #2 (PR #357 review): a freq-type calculation CAN be
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText("Frequency calculation deposited; no imaginary-mode result recorded.")).toBeVisible()
        expect(screen.queryByText("No frequency calculation deposited for this entry.")).not.toBeInTheDocument()
    })

    // Rewritten per BLOCKING #2: the omitted-field and explicit-null cases
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText(/well supported/)).toBeVisible()
        expect(screen.getByText(/24\/26/)).toBeVisible()
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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

    // The structure assessment row (#723): the assessment's own answer to a fixed question, in plain words.
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
            /No saddle target is declared, so the structure assessment has nothing to assess\./],
        [{ status: "not_assessed_over_bound", refusal_code: "structure_selection_evidence_too_large" },
            /Not run: the evidence is over the server's size limits/],
        [{ status: "not_assessed_on_lists" }, /Not assessed on lists\./],
    ])("structure assessment: %j renders its sentence", async (block, sentence) => {
        assessmentWith(block)
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText("Structure assessment")).toBeVisible()
        expect(screen.getByText(sentence)).toBeVisible()
    })

    it("structure assessment: a status token this page was not taught renders as itself and the page still loads", async () => {
        assessmentWith({ status: "some_future_status" })
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText("some_future_status")).toBeVisible()
        expect(screen.queryByText(/malformed/i)).not.toBeInTheDocument()
    })

    it("structure assessment: an outcome token this page was not taught renders as itself and the page still loads", async () => {
        assessmentWith({ status: "assessed", outcome: "some_future_outcome" })
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText(/some_future_outcome/)).toBeVisible()
        expect(screen.queryByText(/malformed/i)).not.toBeInTheDocument()
    })

    it("structure assessment: an older deployment that does not serve the block renders no row", async () => {
        assessmentWith(undefined)
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText("some_future_server_warning — needs investigation")).toBeVisible()
    })

    it("trust warnings: an unknown outcome value still renders the page and the known warnings", async () => {
        trustWith({ some_future_check: "needs_review", no_frequency_result_has_undesignated_imaginary_modes: "warning" })
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.queryByText(/malformed/i)).not.toBeInTheDocument()
        expect(within(screen.getByRole("list", { name: "Trust warnings" })).getAllByRole("listitem")).toHaveLength(1)
    })

    it("trust warnings: renders no list when nothing is a warning, or when the check map is not served", async () => {
        trustWith({ transition_state_entry_present: "passed", imaginary_frequency_value_present: "not_applicable" })
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.queryByRole("list", { name: "Trust warnings" })).not.toBeInTheDocument()
        cleanup()
        trustWith(undefined)
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

        expect(screen.queryByRole("heading", { name: "Levels of theory by stage" })).not.toBeInTheDocument()

        const calcTable = screen.getByRole("table", { name: `Calculations for ${ENTRY_REF}` })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        const calcTable = screen.getByRole("table", { name: `Calculations for ${ENTRY_REF}` })
        const refCells = within(calcTable).getAllByRole("link").map((link) => link.textContent)
        expect(refCells).toEqual(["calc_opt", "calc_freq", "calc_sp", "calc_irc"])
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

        const ircSummary = document.querySelector(".tse-irc-summary") as HTMLElement
        expect(within(ircSummary).getByText(/IRC ran/)).toBeVisible()
        expect(ircSummary).toHaveTextContent("3 forward")
        expect(ircSummary).toHaveTextContent("2 reverse")
        expect(within(ircSummary).getByRole("link", { name: "calc_heds3dt3dsqxmqkcchibvbtwi4" }))
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        const ircSummary = document.querySelector(".tse-irc-summary") as HTMLElement
        expect(within(ircSummary).getByText("No IRC calculation was deposited for this entry.")).toBeVisible()
    })

    it("IRC: renders a passed validation record distinctly from 'ran but no evidence'", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord({ validation: { irc: "present" } }) })
        )))
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText(/IRC validation passed/)).toBeVisible()
    })

    it("IRC: renders a failed validation record distinctly", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord({ validation: { irc: "failed" } }) })
        )))
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText(/IRC validation failed/)).toBeVisible()
    })

    it("lists calculation and geometry evidence linking to their own record pages", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

        expect(screen.getByRole("link", { name: "calc_yu6nspewwco74qzh6lwgh6ewxy" }))
            .toHaveAttribute("href", "/calculations/calc_yu6nspewwco74qzh6lwgh6ewxy")
        expect(screen.getAllByRole("link", { name: "calc_heds3dt3dsqxmqkcchibvbtwi4" })[0])
            .toHaveAttribute("href", "/calculations/calc_heds3dt3dsqxmqkcchibvbtwi4")

        // The final (saddle-point) geometry is always visible, led with its
        // own label. The single `irc_reverse` point sits behind its
        // direction's disclosure -- named on the closed summary by role,
        // count AND atom count now.
        expect(screen.getByRole("link", { name: "geom_mtihqs7ac7btur4efqushl7aoy" }))
            .toHaveAttribute("href", "/geometries/geom_mtihqs7ac7btur4efqushl7aoy")
        expect(screen.getByText(/^final/)).toBeVisible()
        expect(screen.getByText(/IRC reverse.*1 point.*18 atoms/)).toBeVisible()
    })

    it("keeps the transition-state ref behind a References disclosure, collapsed by default -- with no duplicate reaction link inside it", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        expect(screen.getAllByRole("link", { name: "rxn_nu4c52up4c4hqtbtxufwbscq3a" })).toHaveLength(1)
    })

    it("renders an entry whose owning transition state is withheld (profile=curated): no transition-state ref, one reference row", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord({ transition_state: null }) })
        )))
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.queryByText("ts_uql5lf3xeqnehtostrilmns5yi")).not.toBeInTheDocument()
        expect(screen.getByText(bySummaryText(/References \(1\)/))).toBeVisible()
    })

    it("shows this entry's OWN ref first in the identity block, at rest, with the data face and a copy button -- and never inside the References disclosure", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

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
        expect(await screen.findByText("Transition state entry not found")).toBeVisible()
    })

    it("never states the depositor label anywhere, and states the charge/multiplicity fact exactly once", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

        // "TS0" appears zero times on the page -- house rule widened
        // 2026-09: no depositor-typed labels on public pages at all.
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

        expect(screen.getByText("Saddle-point geometry")).toBeVisible()
        expect(screen.getByRole("link", { name: "geom_final" })).toHaveAttribute("href", "/geometries/geom_final")

        expect(screen.getByText(/IRC forward.*3 points.*18 atoms/)).toBeVisible()
        expect(screen.getByText(/IRC reverse.*2 points.*18 atoms/)).toBeVisible()

        expect(screen.getByRole("link", { name: "geom_fwd_1" })).not.toBeVisible()
        expect(screen.getByRole("link", { name: "geom_rev_1" })).not.toBeVisible()

        for (const summary of screen.getAllByText(/IRC (forward|reverse)/)) {
            await userEvent.setup().click(summary)
        }
        expect(screen.getByRole("link", { name: "geom_fwd_1" })).toBeVisible()
        expect(screen.getByRole("link", { name: "geom_rev_1" })).toBeVisible()
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getAllByRole("link", { name: "geom_shared" })).toHaveLength(1)
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

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
                            // the row above -- MEASURED report: three
                            // indistinguishable "TS4 · ... · NOT REVIEWED"
                            // rows on the hydrazine reaction's own page. The
                            // label is not rendered at all any more (house
                            // rule widened 2026-09); distinguishable by ref
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })

        // Excludes itself -- only the other two entries show. Neither
        // depositor label ("TS0"/"TS1") renders anywhere in the section --
        // house rule widened 2026-09: no depositor-typed labels on public
        // pages.
        const siblingsSection = await screen.findByText("Other saddle points deposited for this reaction")
        const section = siblingsSection.closest("section") as HTMLElement
        expect(within(section).queryByText("TS0")).not.toBeInTheDocument()
        expect(within(section).queryByText("TS1")).not.toBeInTheDocument()

        // The two same-label, same-lot, same-software siblings are told
        // apart by their own ref (now the link text itself) and deposited
        // date.
        const linkSibling1 = within(section).getByRole("link", { name: "tse_sibling" })
        expect(linkSibling1).toHaveAttribute("href", "/transition-state-entries/tse_sibling")
        const linkSibling2 = within(section).getByRole("link", { name: "tse_sibling_2" })
        expect(linkSibling2).toHaveAttribute("href", "/transition-state-entries/tse_sibling_2")

        expect(within(section).getByText(/deposited 2026-08-02/)).toBeVisible()
        expect(within(section).getByText(/deposited 2026-07-15/)).toBeVisible()

        expect(within(section).getAllByText(/MRCI\+Davidson/)).toHaveLength(2)
        expect(within(section).getAllByText(/Molpro \(version not recorded\)/)).toHaveLength(2)
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(await screen.findByText("Could not load sibling saddle points for this reaction.")).toBeVisible()
    })

    it("siblings: omits the section entirely when the reaction has none", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        // defaultSiblingsHandler already serves `{ records: [] }`.
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.queryByText("Other saddle points deposited for this reaction")).not.toBeInTheDocument()
    })

    it("review history: collapses a lone default 'not reviewed / not recorded / not recorded' row to one sentence", async () => {
        server.use(http.get(`/api/v1/scientific/transition-state-entries/${ENTRY_REF}`, () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText("Not yet reviewed — no review events recorded.")).toBeVisible()
        expect(screen.queryByRole("table", { name: `Review history for ${ENTRY_REF}` })).not.toBeInTheDocument()
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        const table = screen.getByRole("table", { name: `Review history for ${ENTRY_REF}` })
        expect(within(table).getByText("Looks correct.")).toBeVisible()
        expect(screen.queryByText("Not yet reviewed — no review events recorded.")).not.toBeInTheDocument()
    })
})

// ---------------------------------------------------------------------------
// Orders and picks on this page that used to be silent: the calculation-table
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
        await screen.findByRole("heading", { name: /C1=C\[C\]2C=CCC2C=C1/ })
        expect(screen.getByText(/Rows are grouped by stage \(opt, freq, sp, irc, path search\)/)).toBeVisible()
        expect(screen.getByText(/not a ranking: no row is preferred over another/)).toBeVisible()
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
        expect(within(section).getByText(/Molpro 6\.0/)).toBeVisible()
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
        expect(within(section).getByText(/2 different levels of theory at the sp stage/)).toBeVisible()
        expect(within(section).queryByText(/ccsd\(t\)-f12/i)).not.toBeInTheDocument()
        expect(within(section).queryByText(/mrci\+davidson/i)).not.toBeInTheDocument()
    })

    it("states the one-calculation rule once, above the sibling lines", async () => {
        serveSiblings([sibling("tse_sib_d", [siblingCalculation("calc_d_sp", "sp", "ccsd(t)-f12", "Molpro")])])
        page()
        const section = (await screen.findByText("Other saddle points deposited for this reaction")).closest("section") as HTMLElement
        expect(within(section).getByText(/read from one calculation of that saddle point/)).toBeVisible()
        expect(within(section).getByText(/This entry is not listed here, though the count above includes it/)).toBeVisible()
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
        expect(within(section).getByRole("link", { name: "tse_many_1" })).toBeVisible()
        await user.click(within(section).getByRole("button", { name: "Next" }))
        expect(await within(section).findByText("Showing 3–4 of 5 saddle points.")).toBeVisible()
        expect(seen).toContain("2:2")
        expect(within(section).getByRole("link", { name: "tse_many_3" })).toBeVisible()
        expect(within(section).queryByRole("link", { name: "tse_many_1" })).not.toBeInTheDocument()
    })
})
