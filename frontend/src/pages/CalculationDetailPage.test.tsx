import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import CalculationDetailPage from "./CalculationDetailPage"
import { resetAllRequestCaches } from "../api/requestCache"
import { bySummaryText } from "../test/disclosureQueries"
// `design-system.css` is normally reached only via `index.css`'s `@import`
// -- see `ReactionEntryPage.test.tsx`'s identical import for why a page
// test rendered in isolation needs it directly to exercise `.t-preserve-
// case`'s real computed style.
import "../design-system.css"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const ENDPOINT = "/api/v1/scientific/calculations/calc_freq_one"

function page() {
    return render(
        <MemoryRouter initialEntries={["/calculations/calc_freq_one"]}>
            <Routes>
                <Route path="/calculations/:calculationRef" element={<CalculationDetailPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

/**
 * Binds a `<dt>` label to its own `<dd>` value by DOM adjacency — see the
 * identical helper's docstring in `EntryThermoSection.test.tsx`.
 */
function ddFor(container: HTMLElement, term: string): string {
    const dt = Array.from(container.querySelectorAll("dt")).find((el) => el.textContent === term)
    if (!dt) throw new Error(`No <dt> with text "${term}" found in this container`)
    return dt.nextElementSibling?.textContent ?? ""
}

/** Waits for the page's own load gate -- the h1 always opens with the
 * type label ("Frequency of ...", "Optimisation of ...", ...), so a
 * prefix match is a stable target regardless of which owner/formula a
 * given test's fixture carries. */
async function findLoaded(typePrefix: string) {
    return screen.findByRole("heading", { level: 1, name: new RegExp(`^${typePrefix} of `) })
}

/**
 * The "Further evidence" disclosure summary text for e.g. "SCF stability"
 * collides with the SAME string as the evidence-checklist `<dt>` above it
 * -- scope every on-demand-section summary lookup to the disclosure group
 * itself so a test never accidentally matches the checklist row instead.
 */
function furtherEvidenceSummary(text: string): HTMLElement {
    const group = document.querySelector(".geometry-groups") as HTMLElement
    return within(group).getByText(text)
}

/**
 * A fully-populated calculation record: a `freq` calculation with a real
 * dependency edge to its parent `opt` (modelled on the live
 * calc_afsfe4g5xtgiq2yjnutaham5iy -> calc_rypxkxvsku5x2nk6sqbhhmfcla
 * `freq_on` edge measured against https://tckdb.homecalvin.com). Every
 * `available_sections` flag defaults `true` except the ones a `freq`
 * calculation genuinely never has (`has_scan`, `has_irc`, `has_path_search`,
 * `has_execution_environment`), so both the "expand and fetch" branch and
 * the "known empty, no request" branch of every on-demand section are
 * reachable from one fixture.
 */
function mockRecord(overrides: Record<string, unknown> = {}) {
    return {
        calculation: {
            calculation_ref: "calc_freq_one",
            type: "freq",
            quality: "raw",
            created_at: "2026-07-21T12:06:50.748258",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        },
        owner: {
            kind: "species_entry",
            species_entry: {
                species_ref: "spc_demo",
                species_entry_ref: "spe_demo",
                species_entry_label: "ground state",
                formula: "CH3",
                canonical_smiles: "[CH3]",
                inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
                charge: 0,
                multiplicity: 2,
                species_entry_kind: "minimum",
                electronic_state_kind: "ground",
            },
            transition_state_entry: null,
        },
        conformer: null,
        level_of_theory: {
            level_of_theory_ref: "lot_1", method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp",
            dispersion: "D3BJ", solvent: "water(CPCM)",
        },
        software_release: { software_release_ref: "srel_1", software: "Gaussian", version: "Gaussian 16, Revision C.02" },
        workflow_tool_release: { workflow_tool_release_ref: "wfr_1", workflow_tool: "ARC", version: "1.1.0" },
        literature: { literature_ref: "lit_1", title: "A study of methyl radicals", year: 2019, doi: "10.1000/xyz" },
        provenance: {
            has_result: true,
            converged: null,
            geometry_validation_status: "not_present",
            scf_stability_status: "not_present",
            submission_ref: "sub_demo",
        },
        available_sections: {
            has_results: true,
            has_dependencies: true,
            has_parameters: true,
            has_constraints: true,
            has_artifacts: true,
            has_input_geometries: true,
            has_output_geometries: true,
            has_geometry_validation: true,
            has_scf_stability: true,
            has_wavefunction_diagnostic: true,
            has_spin_diagnostic: true,
            has_freq_modes: true,
            has_hessian: true,
            has_scan: false,
            has_irc: false,
            has_path_search: false,
            has_execution_environment: false,
            has_energy_corrections: true,
        },
        results: {
            kind: "freq",
            sp: null, opt: null, scan: null, irc: null, path_search: null,
            freq: {
                n_imag: 0, imag_freq_cm1: null, zpe_hartree: 0.0297, zpe_uncertainty_hartree: null,
                reaction_coordinate_mode_index: null, imaginary_mode_tau_cm1: null,
                imaginary_mode_tau_basis: null, imaginary_mode_structural_flag: null,
                n_imag_at_or_above_tau: null,
            },
        },
        dependencies: [
            {
                role: "freq_on",
                direction: "child",
                parent_calculation_ref: "calc_opt_parent",
                child_calculation_ref: "calc_freq_one",
            },
        ],
        input_geometries: [
            { geometry_ref: "geom_input_one", input_order: 1, output_order: null, role: null, natoms: 4, geom_hash: "abc" },
        ],
        output_geometries: [
            { geometry_ref: "geom_output_one", input_order: null, output_order: 1, role: "final", natoms: 4, geom_hash: "def" },
            { geometry_ref: "geom_output_two", input_order: null, output_order: 2, role: "final", natoms: 4, geom_hash: "ghi" },
        ],
        review_history: [
            { status: "not_reviewed", note: null, reviewed_at: null, submission_ref: "sub_demo" },
        ],
        energy_corrections: [{
            application_role: "zpe_correction",
            applied_value: -1.4,
            applied_value_unit: "kcal_mol",
            applied_value_hartree: -0.0022,
            temperature_k: null,
            note: null,
            target_record_type: "species_entry",
            target_record_ref: "spe_demo",
            target_endpoint: null,
            energy_correction_scheme_ref: "ecs_1",
            energy_correction_scheme_name: "Petersson BAC",
            frequency_scale_factor_ref: null,
            component_count: 0,
            components_truncated: false,
            components: [],
        }],
        geometry_validation: [{
            input_geometry_ref: "geom_input_one",
            output_geometry_ref: "geom_output_one",
            species_smiles: "[CH3]",
            formula_matches: true,
            is_isomorphic: true,
            rmsd: 0.01,
            n_mappings: 1,
            validation_status: "passed",
            validation_reason: null,
            rmsd_warning_threshold: 1.0,
        }],
        scf_stability: [{
            status: "stable",
            lowest_eigenvalue: 0.02,
            instability_count: 0,
            instability_type: null,
            reoptimized_wavefunction: null,
            note: null,
            source_calculation_ref: "calc_stability_source",
        }],
        wavefunction_diagnostic: [{
            t1_diagnostic: 0.01, d1_diagnostic: 0.02, t1_norm: null, largest_t2_amplitude: null, note: null,
        }],
        spin_diagnostic: [{
            s_squared: 0.75, s_squared_expected: 0.75, s_squared_annihilated: null, note: null,
        }],
        parameters: [
            { raw_key: "scf_conv", raw_value: "1e-8", canonical_key: "scf_convergence", canonical_value: "1e-8", section: "scf", unit: null },
        ],
        constraints: [
            { constraint_index: 1, constraint_kind: "bond", atom_indices: [1, 2], target_value: 1.09 },
        ],
        freq_modes: [
            { mode_index: 1, frequency_cm1: 537.07, is_imaginary: false, reduced_mass_amu: null, force_constant_mdyne_angstrom: null, imaginary_disposition: null },
        ],
        imaginary_mode_projections: {
            status: "determined",
            modes: [],
            conflict_count: 0,
            natoms: 4,
            is_linear: false,
            rigid_body_overlap_threshold: 0.9,
            torsion_overlap_threshold: 0.7,
        },
        artifacts: [
            { artifact_ref: null, kind: "output_log", uri: "s3://bucket/key", filename: "input.log", sha256: "a".repeat(64), bytes: 72526, created_at: "2026-07-21T12:06:50.748258" },
        ],
        scan: null,
        irc: null,
        path_search: null,
        execution_environment: null,
        ...overrides,
    }
}

describe("CalculationDetailPage", () => {
    // The freq Result block used to print the raw field name
    // "n_imag at or above tau" and, for a null value, "not determinable" --
    // a claim about the data where the archive simply holds no projection.
    it("labels the imaginary-mode projection plainly and reads a missing projection as not recorded", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")

        const row = screen.getByText("Imaginary modes above the noise floor (τ)").closest("div") as HTMLElement
        expect(within(row).getByText("not recorded")).toBeVisible()
        expect(screen.queryByText(/n_imag at or above tau/)).not.toBeInTheDocument()
        expect(screen.queryByText("not determinable")).not.toBeInTheDocument()
    })

    it("renders a stored imaginary-mode projection count in that row", async () => {
        const record = mockRecord()
        ;(record.results as { freq: Record<string, unknown> }).freq.n_imag_at_or_above_tau = 2
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record })))
        page()
        await findLoaded("Frequency")

        const row = screen.getByText("Imaginary modes above the noise floor (τ)").closest("div") as HTMLElement
        expect(within(row).getByText("2")).toBeVisible()
    })

    // 2026-09-04 owner decision: τ is resolved from the freq job's Hessian
    // method (`imaginary_mode_tau_basis`). A null basis (never judged) reads
    // "not recorded" on both new rows, same as every other absent value.
    it("reads a null tau basis as not recorded on both the Hessian method and noise floor τ rows", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        expect(ddFor(resultsSection, "Hessian method")).toBe("not recorded")
        expect(ddFor(resultsSection, "Noise floor τ")).toBe("not recorded")
    })

    it("renders a RECORDED tau basis as its plain-language method, with no assumed language", async () => {
        const record = mockRecord()
        ;(record.results as { freq: Record<string, unknown> }).freq = {
            ...(record.results as { freq: Record<string, unknown> }).freq,
            imaginary_mode_tau_cm1: 15, imaginary_mode_tau_basis: "analytic_tight",
        }
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record })))
        page()
        await findLoaded("Frequency")
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        expect(ddFor(resultsSection, "Hessian method")).toBe("analytic")
        const tauCell = ddFor(resultsSection, "Noise floor τ")
        expect(tauCell).toContain("15 cm⁻¹")
        expect(tauCell).toContain("analytic, tight convergence")
        expect(tauCell).not.toContain("assumed")
    })

    // Copy rule (brief item 4): "assumed" must appear whenever an
    // assumed_* basis is rendered, and never otherwise -- covered from the
    // other direction in `tauBasis.test.ts`; this is the page-level half.
    it("renders an ASSUMED tau basis with a visible 'assumed' word on both rows, same τ as its recorded counterpart", async () => {
        const record = mockRecord()
        ;(record.results as { freq: Record<string, unknown> }).freq = {
            ...(record.results as { freq: Record<string, unknown> }).freq,
            imaginary_mode_tau_cm1: 15, imaginary_mode_tau_basis: "assumed_analytic_default",
        }
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record })))
        page()
        await findLoaded("Frequency")
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        const methodCell = ddFor(resultsSection, "Hessian method")
        expect(methodCell).toContain("analytic")
        expect(methodCell).toContain("assumed")
        const tauCell = ddFor(resultsSection, "Noise floor τ")
        expect(tauCell).toContain("15 cm⁻¹")
        expect(tauCell).toContain("assumed")
    })

    it("shows an unrecognised tau basis token raw rather than hiding it", async () => {
        const record = mockRecord()
        ;(record.results as { freq: Record<string, unknown> }).freq = {
            ...(record.results as { freq: Record<string, unknown> }).freq,
            imaginary_mode_tau_cm1: 15, imaginary_mode_tau_basis: "semi_numerical_v2",
        }
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record })))
        page()
        await findLoaded("Frequency")
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        expect(ddFor(resultsSection, "Hessian method")).toBe("semi_numerical_v2")
        expect(ddFor(resultsSection, "Noise floor τ")).toContain("semi_numerical_v2")
    })

    it("requests exactly the eager section tokens, in the documented order", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include")).toEqual([
                "results", "dependencies", "review", "input_geometries", "output_geometries",
            ])
            return HttpResponse.json({ record: mockRecord() })
        }))
        page()
        expect(await findLoaded("Frequency")).toBeVisible()
    })

    // Item 3: identity, then classification, then provenance -- the h1
    // states WHAT this is (type + the owner's own identity), the eyebrow
    // states the classification fact ("Frequency calculation"), plain
    // provenance rows come after.
    it("renders the h1 as '<Type> of <species formula>' using the Formula component, and the classification fact in the eyebrow", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        const h1 = await findLoaded("Frequency")
        // `Formula` renders element counts as <sub> -- "CH3" becomes "CH"
        // then a subscript "3", so the accessible name collapses the
        // subscript back into plain text ("Frequency of CH3") while the
        // DOM keeps the <sub>.
        expect(h1).toHaveTextContent("Frequency of CH3")
        expect(h1.querySelector("sub")).not.toBeNull()
        expect(h1.querySelector("sub")?.textContent).toBe("3")
        expect(screen.getByText("Frequency calculation · deposited evidence")).toBeVisible()
    })

    it("renders breadcrumb and identity links, and keeps stable refs visible", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")

        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(breadcrumb).getByRole("link", { name: "Species" })).toHaveAttribute("href", "/species/spc_demo")
        expect(within(breadcrumb).getByRole("link", { name: "Species entry" }))
            .toHaveAttribute("href", "/species-entries/spe_demo")

        // The identity header's "Species entry" fact links out using the
        // entry's own formula as its text, followed by the served
        // discriminator expanded via `recordFacets.ts`'s `stereoChip`
        // (never the raw `species_entry_label` string alone) -- see
        // `RecordIdentityHeader.tsx`'s "Species entry" fact comment.
        // `mockRecord()`'s owner formula is "CH3", label "ground state"
        // (not one of the four stereo tokens `stereoChip` expands, so it
        // passes through unchanged).
        expect(screen.getByRole("link", { name: "CH3 · ground state" })).toHaveAttribute("href", "/species-entries/spe_demo")

        // The calculation's own ref is visible at rest, first in the
        // identity block -- never behind the collapsed References
        // disclosure any more (owner decision: "yes show each record's own
        // ref inline").
        const facts = screen.getByText("Calculation ref").closest(".record-identity-facts") as HTMLElement
        expect(facts).not.toBeNull()
        const ownRefValue = within(facts).getByText("calc_freq_one")
        expect(ownRefValue).toBeVisible()
        expect(ownRefValue.tagName).toBe("CODE")
        expect(ownRefValue).toHaveClass("data")
        expect(Array.from(facts.children)[0]).toHaveTextContent("Calculation ref")
        expect(within(facts).getByRole("button", { name: /copy calculation ref/i })).toBeVisible()

        // Not duplicated inside the (collapsed) References disclosure --
        // scoped to the disclosure itself (not a page-wide `getAllByText`),
        // because the Related-calculations graph's centre node ALSO shows
        // this same ref as plain text elsewhere on the page (by design:
        // "showing its type and ref" -- see `CalculationDependencyGraph.tsx`),
        // which would make a page-wide count legitimately 2, not 1.
        const summary = screen.getByText(bySummaryText(/References \(/))
        fireEvent.click(summary)
        const refsDisclosure = summary.closest("details") as HTMLElement
        expect(within(refsDisclosure).queryByText("calc_freq_one")).not.toBeInTheDocument()
    })

    // Item 1 (BLOCKING): a TS-owned calculation's owner now links to the
    // real `/transition-state-entries/:ref` route, plus a breadcrumb
    // branch mirroring `TransitionStateEntryPage`'s own.
    //
    // The fixture's `transition_state_entry.label: "TS0"` is deliberately
    // populated but MUST NOT appear anywhere -- house rule widened 2026-09:
    // no depositor-typed labels on public pages. The h1 and the owner link
    // both read the entry ref instead, whether or not a label was
    // deposited (see the sibling test below for the no-label case, kept as
    // a separate fixture so a regression that starts branching on the
    // wire's `label` field again would only break one of the two, not
    // both identically).
    it("links a transition-state owner to its entry page by ref, with a breadcrumb branch, instead of unlinked plain text or the depositor label", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                owner: {
                    kind: "transition_state_entry",
                    species_entry: null,
                    transition_state_entry: {
                        transition_state_ref: "ts_demo",
                        transition_state_entry_ref: "tse_demo",
                        label: "TS0",
                        charge: 0,
                        multiplicity: 1,
                        status: "candidate",
                        reaction_entry_ref: "rxe_demo",
                    },
                },
            }),
        })))
        page()
        const h1 = await findLoaded("Frequency")
        expect(h1).toHaveTextContent("Frequency of tse_demo")
        expect(h1).not.toHaveTextContent("TS0")

        const link = screen.getByRole("link", { name: "tse_demo" })
        expect(link).toHaveAttribute("href", "/transition-state-entries/tse_demo")
        expect(screen.queryByRole("link", { name: "TS0" })).not.toBeInTheDocument()
        // No more "does not yet have a dedicated page" disclaimer.
        expect(screen.queryByText(/does not yet have a dedicated page/)).not.toBeInTheDocument()

        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        // The "Browse" crumb ahead of the entry link points at the
        // transition-state browse kind's own canonical path now
        // (per-kind browse paths change) -- not the legacy
        // `/species?kind=transition_state`.
        expect(within(breadcrumb).getByRole("link", { name: "Browse" })).toHaveAttribute("href", "/transition-states")
        expect(within(breadcrumb).getByRole("link", { name: "Transition state entry" }))
            .toHaveAttribute("href", "/transition-state-entries/tse_demo")
        // Reaction entry stays plain text -- no route exists for it yet.
        expect(within(breadcrumb).queryByRole("link", { name: "Reaction entry" })).not.toBeInTheDocument()
    })

    // SHOULD-FIX-8 (re-review pass): the ref-fallback path, now also the
    // ONLY path -- `titleSubject` no longer branches on the depositor
    // label at all, so this and the test above must render identically
    // apart from the ref value itself.
    it("renders the raw ref, wrapped as a data run, when the TS owner has no label either", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                owner: {
                    kind: "transition_state_entry",
                    species_entry: null,
                    transition_state_entry: {
                        transition_state_ref: "ts_demo",
                        transition_state_entry_ref: "tse_demo2",
                        label: null,
                        charge: 0,
                        multiplicity: 1,
                        status: "candidate",
                        reaction_entry_ref: null,
                    },
                },
            }),
        })))
        page()
        const h1 = await findLoaded("Frequency")
        expect(h1).toHaveTextContent("Frequency of tse_demo2")

        // The ref renders as a `.data` run inside the h1, not plain text --
        // `.calc-headline-ref` is what keeps its font-size tracking the
        // h1 around it (`calculation-detail.css`) rather than dropping to
        // `.data`'s own 13px baseline.
        const ref = within(h1).getByText("tse_demo2")
        expect(ref.tagName).toBe("CODE")
        expect(ref).toHaveClass("data", "calc-headline-ref")
        // Not a link inside the h1 -- the clickable path to the TS entry
        // is the separate "This calculation belongs to..." sentence below
        // the identity header, unaffected by this fallback.
        expect(within(h1).queryByRole("link")).not.toBeInTheDocument()
    })

    it("surfaces level_of_theory_ref, dispersion and solvent in the provenance row, and moves the refs into the collapsed disclosure", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")

        const context = screen.getByText("Level of theory", { selector: "dt" }).closest(".record-context") as HTMLElement
        expect(ddFor(context, "Dispersion")).toBe("D3BJ")
        expect(ddFor(context, "Solvent")).toBe("water(CPCM)")
        // Level of theory ref/software release ref/workflow tool release
        // ref/literature ref all moved into RefsDisclosure -- the
        // calculation's own ref is NOT among them any more; it renders in
        // the identity header instead (see the "own ref" test above).
        expect(within(context).queryByText("Level of theory ref")).not.toBeInTheDocument()

        fireEvent.click(screen.getByText(bySummaryText(/References \(/)))
        expect(screen.getByText("lot_1")).toBeVisible()
        expect(screen.getByText("srel_1")).toBeVisible()
        expect(screen.getByText("wfr_1")).toBeVisible()
        expect(screen.getByText("lit_1")).toBeVisible()
    })

    // Item 3: Dispersion/Solvent/Literature rows appear ONLY when the
    // archive actually recorded them -- never a "not recorded" row for a
    // field most calculations simply don't carry.
    it("omits Dispersion, Solvent and Literature rows entirely when the archive has nothing for them", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                level_of_theory: { level_of_theory_ref: "lot_1", method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", dispersion: null, solvent: null },
                literature: null,
            }),
        })))
        page()
        await findLoaded("Frequency")
        expect(screen.queryByText("Dispersion")).not.toBeInTheDocument()
        expect(screen.queryByText("Solvent")).not.toBeInTheDocument()
        expect(screen.queryByText("Literature", { selector: "dt" })).not.toBeInTheDocument()
        // Software and workflow tool are NEVER hidden.
        expect(screen.getByText("Software", { selector: "dt" })).toBeVisible()
        expect(screen.getByText("Workflow tool", { selector: "dt" })).toBeVisible()
    })

    it("does not stutter the software/workflow-tool name when the version already opens with it", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")

        const context = screen.getByText("Level of theory", { selector: "dt" }).closest(".record-context") as HTMLElement
        const softwareRow = within(context).getByText("Software", { selector: "dt" }).closest("div") as HTMLElement
        expect(within(softwareRow).getByText("Gaussian 16, Revision C.02")).toBeVisible()
        expect(within(softwareRow).queryByText(/Gaussian Gaussian/)).not.toBeInTheDocument()

        const workflowRow = within(context).getByText("Workflow tool", { selector: "dt" }).closest("div") as HTMLElement
        expect(within(workflowRow).getByText("ARC 1.1.0")).toBeVisible()
    })

    // Review finding (item 4): the "Input geometries / Output geometries /
    // Dependency edges" count tiles rendered cardinalities at display size
    // that each duplicated a section directly below. They are gone; the
    // evidence checklist is the only card left in the summary strip.
    it("renders no count tiles -- the summary strip is the evidence checklist alone", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")

        expect(screen.queryByText("Input geometries")).not.toBeInTheDocument()
        expect(screen.queryByText("Output geometries")).not.toBeInTheDocument()
        expect(screen.queryByText("Dependency edges")).not.toBeInTheDocument()
        expect(document.querySelector(".metric")).toBeNull()
        // Positive check so this cannot pass on an empty page: the checklist
        // card is still there, inside the summary region.
        const strip = screen.getByRole("region", { name: "Calculation evidence summary" })
        expect(within(strip).getByText("Evidence on this calculation")).toBeVisible()
    })

    // Item 6: one sentence per edge, with a FIXED subject, replacing the
    // Relationship/Role/Related-calculation columns that read backwards.
    it("renders a parent-side edge and a child-side edge as different, correctly-subjected sentences", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                dependencies: [
                    {
                        role: "single_point_on", direction: "parent",
                        parent_calculation_ref: "calc_freq_one", child_calculation_ref: "calc_osrf4pnfcesq6s6somn7nr5hly",
                    },
                    {
                        role: "optimized_from", direction: "child",
                        parent_calculation_ref: "calc_htgb7s5nakuw52eqhcxpvilpoq", child_calculation_ref: "calc_freq_one",
                    },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const depSection = screen.getByRole("heading", { name: "Related calculations" }).closest("section") as HTMLElement

        // The section renders the graph itself, not just the sentence
        // list -- review finding: reverting `CalculationDependencyGraph`
        // out of `CalculationDetailPage.tsx` (leaving only the demoted
        // sentence list this test already checks below) left the FULL
        // suite green, since nothing here named the graph specifically.
        expect(within(depSection).getByRole("img", { name: /Dependency graph for/ })).toBeVisible()

        // This page IS the centre calculation's own page -- its centre
        // node must never become a link (unlike ReactionTransitionStates
        // Section.tsx's own graph, whose centre is a DIFFERENT calculation
        // the reader is not already viewing). Guarded here at the call
        // site (`CalculationDependencyGraph.tsx:884` never passes
        // `centreLinked`), not just at that prop's own default, so an
        // accidental opt-in on THIS page is caught even if the component's
        // default ever changed.
        const centreNode = within(depSection).getByTestId("dep-node-centre-calc_freq_one")
        expect(within(centreNode).queryByRole("link")).toBeNull()

        // This page has no "whose" to report about its own centre
        // calculation (unlike `ReactionTransitionStatesSection.tsx`'s own
        // graph, whose centre node names the TS entry it belongs to) --
        // `CalculationDependencyGraph.tsx`'s `centreSubject` must never be
        // passed at this call site, so the node stays exactly two rows
        // (pill + ref), never a third subject row.
        expect(within(centreNode).queryByTestId("dep-node-subject-calc_freq_one")).toBeNull()

        // Parent-side: the OTHER calculation is the subject, and the
        // sentence has a verb ("was run on") -- review finding: it used to
        // read "single point on this geometry" with no verb at all.
        const spItem = within(depSection).getByRole("link", { name: "calc_osrf4pnfcesq6s6somn7nr5hly" }).closest("li") as HTMLElement
        expect(spItem.textContent).toBe("The single point was computed on this geometry by calc_osrf4pnfcesq6s6somn7nr5hly")

        // Child-side optimized_from: THIS calculation is the subject, fixed sentence.
        const optFromItem = within(depSection).getByRole("link", { name: "calc_htgb7s5nakuw52eqhcxpvilpoq" }).closest("li") as HTMLElement
        expect(optFromItem.textContent).toBe(
            "This is the fine optimisation; its starting geometry came from calc_htgb7s5nakuw52eqhcxpvilpoq",
        )

        // The old backwards "feeds into | optimized from | ref" table is gone.
        expect(within(depSection).queryByText("feeds into")).not.toBeInTheDocument()
        expect(within(depSection).queryByRole("columnheader")).not.toBeInTheDocument()
    })

    it("renders a freq_on parent-side edge with its own fixed sentence", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                dependencies: [
                    {
                        role: "freq_on", direction: "parent",
                        parent_calculation_ref: "calc_freq_one", child_calculation_ref: "calc_rypxkxvsku5x2nk6sqbhhmfcla",
                    },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const depSection = screen.getByRole("heading", { name: "Related calculations" }).closest("section") as HTMLElement
        const item = within(depSection).getByRole("link", { name: "calc_rypxkxvsku5x2nk6sqbhhmfcla" }).closest("li") as HTMLElement
        expect(item.textContent).toBe("Frequencies were computed on this geometry by calc_rypxkxvsku5x2nk6sqbhhmfcla")
    })

    // Review finding (BLOCKING-1): child-side edges used to say "This was
    // optimized from <link>" for EVERY role, not only `optimized_from` --
    // the live archive has child-side `freq_on` (a freq calc's edge back to
    // the opt it ran on), `single_point_on`, and `irc_start` edges, and each
    // one rendered a sentence claiming the calc was an optimisation of its
    // parent, which is false. Each of these three gets its own sentence
    // with THIS calculation as the fixed subject and the correct verb.
    it("renders a freq_on child-side edge naming what THIS calc is, not 'optimized from'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                dependencies: [
                    {
                        role: "freq_on", direction: "child",
                        parent_calculation_ref: "calc_ts0_opt", child_calculation_ref: "calc_freq_one",
                    },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const depSection = screen.getByRole("heading", { name: "Related calculations" }).closest("section") as HTMLElement
        const item = within(depSection).getByRole("link", { name: "calc_ts0_opt" }).closest("li") as HTMLElement
        expect(item.textContent).toBe("This frequency calculation was computed on the geometry from calc_ts0_opt")
        expect(item.textContent).not.toMatch(/optimized from/)
    })

    it("renders a single_point_on child-side edge naming what THIS calc is, not 'optimized from'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                dependencies: [
                    {
                        role: "single_point_on", direction: "child",
                        parent_calculation_ref: "calc_ts0_opt", child_calculation_ref: "calc_freq_one",
                    },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const depSection = screen.getByRole("heading", { name: "Related calculations" }).closest("section") as HTMLElement
        const item = within(depSection).getByRole("link", { name: "calc_ts0_opt" }).closest("li") as HTMLElement
        expect(item.textContent).toBe("This single point was computed on the geometry from calc_ts0_opt")
        expect(item.textContent).not.toMatch(/optimized from/)
    })

    it("renders an irc_start child-side edge naming what THIS calc is, not 'optimized from'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                dependencies: [
                    {
                        role: "irc_start", direction: "child",
                        parent_calculation_ref: "calc_ts0_opt", child_calculation_ref: "calc_freq_one",
                    },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const depSection = screen.getByRole("heading", { name: "Related calculations" }).closest("section") as HTMLElement
        const item = within(depSection).getByRole("link", { name: "calc_ts0_opt" }).closest("li") as HTMLElement
        expect(item.textContent).toBe("This IRC started from the geometry of calc_ts0_opt")
        expect(item.textContent).not.toMatch(/optimized from/)
    })

    it("falls back to the raw role token for an unrecognised child-side role, never to 'optimized from'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                dependencies: [
                    {
                        role: "some_future_role", direction: "child",
                        parent_calculation_ref: "calc_ts0_opt", child_calculation_ref: "calc_freq_one",
                    },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const depSection = screen.getByRole("heading", { name: "Related calculations" }).closest("section") as HTMLElement
        const item = within(depSection).getByRole("link", { name: "calc_ts0_opt" }).closest("li") as HTMLElement
        expect(item.textContent).toMatch(/some future role/)
        expect(item.textContent).not.toMatch(/optimized from/)
    })

    it("shows no dependency edge when the archive returns none", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                dependencies: [],
                available_sections: { ...mockRecord().available_sections, has_dependencies: false },
            }),
        })))
        page()
        await findLoaded("Frequency")

        const depSection = screen.getByRole("heading", { name: "Related calculations" }).closest("section") as HTMLElement
        expect(within(depSection).getByText("No dependency edges are recorded for this calculation.")).toBeVisible()
        expect(within(depSection).queryByRole("link")).not.toBeInTheDocument()
    })

    it("flags a contradiction when the archive marks dependency evidence present but returns none", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord({ dependencies: [] }) })))
        page()
        await findLoaded("Frequency")
        expect(await screen.findByText(
            (_, element) => element?.tagName === "P"
                && (element.textContent ?? "").includes("The archive marks this calculation as having recorded evidence here"),
        )).toBeVisible()
    })

    it("treats an absent dependencies key as 'not requested', distinct from a genuinely empty one", async () => {
        const withoutKey = mockRecord()
        delete (withoutKey as Record<string, unknown>).dependencies
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: withoutKey })))
        page()
        await findLoaded("Frequency")
        const depSection = screen.getByRole("heading", { name: "Related calculations" }).closest("section") as HTMLElement
        expect(within(depSection).getByText("This section was not requested for this view.")).toBeVisible()
        expect(within(depSection).queryByText("No dependency edges are recorded for this calculation."))
            .not.toBeInTheDocument()
    })

    // Item 4: the checklist now carries only Geometry validation and SCF
    // stability, each showing the actual recorded OUTCOME.
    it("shows Geometry validation and SCF stability as their own labelled rows with the real outcome, and drops Result/Convergence", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                provenance: {
                    has_result: true, converged: true,
                    geometry_validation_status: "passed",
                    scf_stability_status: "unstable",
                },
            }),
        })))
        page()
        await findLoaded("Frequency")

        const checklist = document.querySelector(".coverage-checklist") as HTMLElement
        expect(checklist.querySelectorAll("dt")).toHaveLength(2)
        expect(ddFor(checklist, "Geometry validation")).toBe("passed")
        expect(ddFor(checklist, "SCF stability")).toBe("unstable")
        expect(within(checklist).queryByText("Result")).not.toBeInTheDocument()
        expect(within(checklist).queryByText("Convergence")).not.toBeInTheDocument()
    })

    // Independent review: every other assertion against `.coverage-checklist`
    // in this file reads `textContent` (via `ddFor`), which is true
    // regardless of whether the card is open -- none of them prove a
    // READER can actually see these rows. `EvidenceChecklist` collapses
    // this card by default (item 2), so confirm here, with a real
    // visibility check, that opening it (the only way a reader reaches
    // these rows) surfaces the same content the textContent-only
    // assertions above already pin.
    it("the checklist card is collapsed by default, and opening it makes the rows actually visible to a reader", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                provenance: {
                    has_result: true, converged: true,
                    geometry_validation_status: "passed",
                    scf_stability_status: "unstable",
                },
            }),
        })))
        page()
        await findLoaded("Frequency")

        const card = document.querySelector(".card.card--derived.coverage-card") as HTMLElement
        const details = card.querySelector("details") as HTMLDetailsElement
        const checklist = card.querySelector(".coverage-checklist") as HTMLElement
        expect(details.open).toBe(false)
        expect(checklist).not.toBeVisible()

        fireEvent.click(details.querySelector("summary")!)
        expect(details.open).toBe(true)
        expect(checklist).toBeVisible()
        const gvDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Geometry validation")
        const gvDd = gvDt?.nextElementSibling as HTMLElement
        expect(gvDd).toBeVisible()
        expect(gvDd.textContent).toBe("passed")
    })

    // record-summary-row PR, post-review (owner decision: every status word
    // is a pill on every page, no exception for this one): this page's
    // evidence card renders through the SHARED `components/
    // EvidenceChecklist.tsx` component -- confirmed here via its
    // `data-component="evidence-checklist"` marker (not a page-local class
    // or copy this page could drift from independently), and its two rows
    // carry the SAME pill classes `ConformerObservationPage.tsx`'s own
    // present/absent checklist uses: a real recorded outcome ("passed",
    // "unstable") is a plain `.value-pill`, and the absent/not-applicable
    // case is `.value-pill.value-pill--muted` (see `evidenceTone`'s own
    // docstring in `CalculationDetailPage.tsx`).
    it("renders through the shared EvidenceChecklist component, with both rows pilled per the one-rule tone convention", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "sp" },
                provenance: {
                    has_result: true, result_applicable: true,
                    // Geometry validation: not applicable to an `sp` calc
                    // -> muted. SCF stability: a real recorded outcome
                    // -> plain.
                    geometry_validation_status: "not_present", geometry_validation_applicable: false,
                    scf_stability_status: "unstable",
                },
            }),
        })))
        page()
        await findLoaded("Single-point")

        const card = document.querySelector(".card.card--derived.coverage-card") as HTMLElement
        expect(card).not.toBeNull()
        expect(card).toHaveAttribute("data-component", "evidence-checklist")

        const checklist = card.querySelector(".coverage-checklist") as HTMLElement
        const gvDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Geometry validation")
        const gvPill = gvDt?.nextElementSibling?.querySelector("span") as HTMLElement
        expect(gvPill).toHaveClass("value-pill", "value-pill--muted")
        expect(gvPill).toHaveTextContent("not applicable")

        const scfDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "SCF stability")
        const scfPill = scfDt?.nextElementSibling?.querySelector("span") as HTMLElement
        expect(scfPill).toHaveClass("value-pill")
        expect(scfPill).not.toHaveClass("value-pill--muted")
        expect(scfPill).toHaveTextContent("unstable")
    })

    // Item 3 (post-review): the `<dl>` must carry the shared `kv-list`
    // class, not just its own `coverage-checklist` marker class -- without
    // `kv-list`, this element has NO layout rules of its own any more
    // (`calculation-detail.css`'s `.coverage-checklist` is bare margin
    // since this fix), so a revert of the className alone left this suite
    // green with no visible layout. This assertion is what makes that
    // revert fail.
    it("the evidence checklist <dl> carries the shared kv-list class", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")
        const checklist = document.querySelector(".coverage-checklist") as HTMLElement
        expect(checklist.tagName).toBe("DL")
        expect(checklist).toHaveClass("kv-list")
    })

    it("reads geometry validation as 'not applicable' for a type that cannot have it, 'absent' when applicable but unrecorded", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "sp" },
                provenance: {
                    has_result: true, result_applicable: true,
                    geometry_validation_status: "not_present", geometry_validation_applicable: false,
                    scf_stability_status: "not_present",
                },
            }),
        })))
        const notApplicable = page()
        await findLoaded("Single-point")
        const notApplicableText = ddFor(document.querySelector(".coverage-checklist") as HTMLElement, "Geometry validation")
        notApplicable.unmount()
        cleanup()
        // Same `calculationRef` ("calc_freq_one"), a second server scenario --
        // `useCalculation`'s request is now cached across a remount (see
        // `api/requestCache.ts`), which is correct for a real page visit but
        // wrong for this test's technique of replaying two different server
        // states against one ref. Clear it so the second `page()` below
        // actually reaches its own handler instead of replaying the first.
        resetAllRequestCaches()

        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "opt" },
                provenance: {
                    has_result: true, result_applicable: true,
                    geometry_validation_status: "not_present", geometry_validation_applicable: true,
                    scf_stability_status: "not_present",
                },
            }),
        })))
        page()
        await findLoaded("Optimisation")
        const absentText = ddFor(document.querySelector(".coverage-checklist") as HTMLElement, "Geometry validation")

        expect(notApplicableText).toBe("not applicable")
        expect(absentText).toBe("absent")
    })

    it("renders an available on-demand section as idle (not fetched, not empty) until it is opened", async () => {
        let requestCount = 0
        server.use(http.get(ENDPOINT, () => {
            requestCount += 1
            return HttpResponse.json({ record: mockRecord() })
        }))
        page()
        await findLoaded("Frequency")

        const summary = screen.getByText("Parsed parameters")
        const section = summary.closest("details") as HTMLDetailsElement
        expect(section).not.toBeNull()
        expect(section.open).toBe(false)
        // Design/foundations PR B (item 5): every on-demand section now
        // composes the shared `Disclosure` primitive (`.disclosure`,
        // `design-system.css`) instead of a page-local bordered `<details>`
        // -- mutation check: rename this class away and this test fails.
        // The redundant "SHOW" affordance span is gone with it (the
        // primitive's own chevron, `.disclosure > summary::before`, is the
        // idle-state affordance now) -- "Show" must not appear anywhere in
        // this section.
        expect(section).toHaveClass("disclosure")
        expect(within(section).queryByText("Show")).not.toBeInTheDocument()
        expect(within(section).queryByText(/The archive returned no parameter rows/)).not.toBeInTheDocument()
        expect(requestCount).toBe(1) // only the eager fetch so far

        fireEvent.click(summary)
        expect(await within(section).findByText("scf_convergence")).toBeVisible()
        expect(requestCount).toBe(2)

        // Re-toggling does not re-request. See the original test's own
        // comment on why this needs an explicit flush, not a `waitFor`.
        fireEvent.click(summary)
        fireEvent.click(summary)
        await new Promise((resolve) => setTimeout(resolve, 50))
        expect(requestCount).toBe(2)
    })

    it("requests exactly the opened section's own token, nothing else", async () => {
        const requestedIncludeSets: string[][] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            requestedIncludeSets.push(new URL(request.url).searchParams.getAll("include"))
            return HttpResponse.json({ record: mockRecord() })
        }))
        page()
        await findLoaded("Frequency")
        fireEvent.click(furtherEvidenceSummary("Geometry validation"))
        const caveat = await screen.findByText(
            (_, element) => element?.tagName === "P"
                && (element.textContent ?? "").includes("is_isomorphic")
                && (element.textContent ?? "").includes("formula_matches")
                && (element.textContent ?? "").includes("same stored verdict under two names"),
        )
        expect(caveat).toBeVisible()
        expect(screen.getByText("is_isomorphic (legacy name, same value)")).toBeVisible()

        expect(requestedIncludeSets).toEqual([
            ["results", "dependencies", "review", "input_geometries", "output_geometries"],
            ["geometry_validation"],
        ])
    })

    it("gives a failed section fetch its own status message, distinct from every empty state", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            if (includes.includes("parameters")) {
                return HttpResponse.json({ detail: "internal error" }, { status: 500 })
            }
            return HttpResponse.json({ record: mockRecord() })
        }))
        page()
        await findLoaded("Frequency")

        const summary = screen.getByText("Parsed parameters")
        const section = summary.closest("details") as HTMLElement
        fireEvent.click(summary)

        const status = await within(section).findByText("internal error")
        expect(status.getAttribute("role")).toBe("status")
        expect(within(section).queryByText(/The archive returned no parameter rows/)).not.toBeInTheDocument()
        expect(within(section).queryByText("Parsed parameters loaded.")).not.toBeInTheDocument()
    })

    // Item 10: every on-demand section is grouped under ONE "Further
    // evidence" heading -- no per-section heading of its own.
    it("groups every on-demand section under one 'Further evidence' heading, not one heading each", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")
        expect(screen.getByRole("heading", { name: "Further evidence" })).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Parsed parameters" })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Artifacts" })).not.toBeInTheDocument()
        // The disclosures themselves are still there, as plain summaries.
        expect(screen.getByText("Parsed parameters").closest("details")).not.toBeNull()
        expect(screen.getByText("Artifacts").closest("details")).not.toBeNull()
    })

    it("folds a section available_sections marks empty into the shared missing-sections line, inside Further evidence -- not its own landmark", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")

        expect(screen.queryByText("Scan trajectory")).not.toBeInTheDocument()

        const note = screen.getByText(/^Not recorded on this calculation:/)
        expect(note.className).toBe("empty-projection")
        for (const missing of ["Scan trajectory", "IRC trajectory", "Path-search trajectory", "Execution environment"]) {
            expect(note.textContent).toContain(missing)
        }
        // Lives inside the Further evidence section, not a separate
        // `aria-label` landmark of its own.
        const furtherEvidence = screen.getByRole("heading", { name: "Further evidence" }).closest("section") as HTMLElement
        expect(furtherEvidence.contains(note)).toBe(true)
        expect(screen.queryByLabelText("Sections with nothing recorded")).not.toBeInTheDocument()

        // A section this fixture DOES have data for keeps its own disclosure.
        expect(furtherEvidenceSummary("SCF stability").closest("details")).not.toBeNull()
        expect(note.textContent).not.toContain("SCF stability")
    })

    it("folds imaginary-mode projections into the same shared missing-sections line when freq_modes_applicable, gated on has_hessian for content", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({ available_sections: { ...mockRecord().available_sections, has_hessian: false } }),
        })))
        page()
        await findLoaded("Frequency")
        const summaries = Array.from(document.querySelectorAll(".geometry-groups .disclosure summary")).map((el) => el.textContent)
        expect(summaries).not.toContain("Imaginary-mode projections")
        const note = screen.getByText(/^Not recorded on this calculation:/)
        expect(note.textContent).toContain("Imaginary-mode projections")
    })

    // Item 4: imaginary-mode projections is never claimed "missing" for a
    // type that cannot have frequency modes at all.
    it("never lists imaginary-mode projections as missing for a type where freq_modes_applicable is false", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "sp" },
                results: {
                    kind: "sp",
                    sp: { electronic_energy_hartree: -76.1, electronic_energy_uncertainty_hartree: null },
                    opt: null, freq: null, scan: null, irc: null, path_search: null,
                },
                available_sections: {
                    ...mockRecord().available_sections,
                    has_hessian: false, has_freq_modes: false, freq_modes_applicable: false,
                    has_geometry_validation: false, geometry_validation_applicable: false,
                    has_constraints: false, constraints_applicable: false,
                    has_scan: false, scan_applicable: false,
                    has_irc: false, irc_applicable: false,
                    has_path_search: false, path_search_applicable: false,
                    has_execution_environment: false,
                },
            }),
        })))
        page()
        await findLoaded("Single-point")
        const note = screen.getByText(/^Not recorded on this calculation:/)
        expect(note.textContent).not.toContain("Imaginary-mode projections")
        expect(note.textContent).not.toContain("Vibrational modes")
    })

    // Item 4: wavefunction diagnostic is never claimed "missing" -- there is
    // no server-side applicability flag for it, so this client never
    // guesses.
    it("never lists wavefunction diagnostic as missing, even on a plain DFT opt with none recorded", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "opt" },
                results: {
                    kind: "opt",
                    opt: { converged: true, n_steps: 5, final_energy_hartree: -76.1 },
                    sp: null, freq: null, scan: null, irc: null, path_search: null,
                },
                available_sections: { ...mockRecord().available_sections, has_wavefunction_diagnostic: false },
            }),
        })))
        page()
        await findLoaded("Optimisation")
        const note = screen.getByText(/^Not recorded on this calculation:/)
        expect(note.textContent).not.toContain("Wavefunction diagnostic")
    })

    it("surfaces the provenance pointers on the on-demand sections, not just their headline fields", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")

        fireEvent.click(furtherEvidenceSummary("SCF stability"))
        const scfSection = furtherEvidenceSummary("SCF stability").closest("details") as HTMLElement
        expect(await within(scfSection).findByRole("link", { name: "calc_stability_source" }))
            .toHaveAttribute("href", "/calculations/calc_stability_source")

        fireEvent.click(furtherEvidenceSummary("Geometry validation"))
        const gvSection = furtherEvidenceSummary("Geometry validation").closest("details") as HTMLElement
        expect(await within(gvSection).findByRole("link", { name: "geom_input_one" }))
            .toHaveAttribute("href", "/geometries/geom_input_one")

        fireEvent.click(screen.getByText("Artifacts"))
        const artifactSection = screen.getByText("Artifacts").closest("details") as HTMLElement
        expect(await within(artifactSection).findByText("a".repeat(64))).toBeVisible()

        fireEvent.click(screen.getByText("Energy corrections"))
        const ecSection = screen.getByText("Energy corrections").closest("details") as HTMLElement
        expect(await within(ecSection).findByText("ecs_1")).toBeVisible()
    })

    describe("Artifacts section artifact ref cell", () => {
        // calculation_artifact now carries a public_ref (backend public-ref
        // backfill): a populated ref renders in the same code face as every
        // other ref on this page, with the shared copy button next to it --
        // not bare text, and not the old "not recorded" placeholder.
        it("renders a populated artifact_ref in the data face with a copy button", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({
                record: mockRecord({
                    artifacts: [
                        { artifact_ref: "art_9f3k2q7m1x8h4v6c0b5n3z2y7t", kind: "output_log", uri: "s3://bucket/key", filename: "input.log", sha256: "a".repeat(64), bytes: 72526, created_at: "2026-07-21T12:06:50.748258" },
                    ],
                }),
            })))
            page()
            await findLoaded("Frequency")

            fireEvent.click(screen.getByText("Artifacts"))
            const artifactSection = screen.getByText("Artifacts").closest("details") as HTMLElement
            const row = (await within(artifactSection).findByText("art_9f3k2q7m1x8h4v6c0b5n3z2y7t")).closest("tr") as HTMLElement
            expect(within(row).getByText("art_9f3k2q7m1x8h4v6c0b5n3z2y7t").tagName).toBe("CODE")
            expect(within(row).getByRole("button", { name: "Copy artifact ref" })).toBeVisible()
            expect(within(row).queryByText("no ref")).not.toBeInTheDocument()
            expect(within(row).getByText("a".repeat(64))).toHaveClass("data")
        })

        it("reads a null artifact_ref as 'no ref', with no copy button", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({
                record: mockRecord({
                    artifacts: [
                        { artifact_ref: null, kind: "output_log", uri: "s3://bucket/key", filename: "input.log", sha256: "a".repeat(64), bytes: 72526, created_at: "2026-07-21T12:06:50.748258" },
                    ],
                }),
            })))
            page()
            await findLoaded("Frequency")

            fireEvent.click(screen.getByText("Artifacts"))
            const artifactSection = screen.getByText("Artifacts").closest("details") as HTMLElement
            const row = (await within(artifactSection).findByText("input.log")).closest("tr") as HTMLElement
            expect(within(row).getByText("no ref")).toBeVisible()
            expect(within(row).queryByRole("button", { name: /Copy/ })).not.toBeInTheDocument()
        })
    })

    it("marks a result whose shape this view does not recognise, rather than rendering an empty result section", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({ results: { kind: "sp", sp: null, opt: null, freq: null, scan: null, irc: null, path_search: null } }),
        })))
        page()
        await findLoaded("Frequency")
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        const notice = within(resultsSection).getByRole("alert")
        expect(notice).toHaveTextContent(/not recognised/)
        expect(resultsSection.querySelector(".kv-list")).toBeNull()
    })

    it("formats an sp result's electronic energy at its own 6dp spec, not the 4dp frequency-scale-factor spec", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                results: {
                    kind: "sp",
                    sp: { electronic_energy_hartree: -76.1234567, electronic_energy_uncertainty_hartree: null },
                    opt: null, freq: null, scan: null, irc: null, path_search: null,
                },
            }),
        })))
        page()
        await findLoaded("Frequency")
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        expect(ddFor(resultsSection, "Electronic energy (hartree)")).toBe("-76.123457")
    })

    // Item 4: the opt Result body drops "Final energy" (it is the page's
    // own headline now) and relabels Steps.
    it("drops the opt Result section's own Final energy row and relabels Steps", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "opt" },
                results: {
                    kind: "opt",
                    opt: { converged: true, n_steps: 12, final_energy_hartree: -76.1234567 },
                    sp: null, freq: null, scan: null, irc: null, path_search: null,
                },
            }),
        })))
        page()
        await findLoaded("Optimisation")
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        expect(within(resultsSection).queryByText("Final energy (hartree)")).not.toBeInTheDocument()
        expect(ddFor(resultsSection, "Optimiser steps (parsed from log)")).toBe("12")
        expect(ddFor(resultsSection, "Converged")).toBe("Yes")
        // The number itself only appears once now -- as the headline.
        const headline = document.querySelector(".calc-headline-energy") as HTMLElement
        expect(within(headline).getByTestId("energy-display-value")).toHaveTextContent("-76.123457")
    })

    it("binds a scan's min and max electronic energy to their own labelled row — never swapped", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                available_sections: { ...mockRecord().available_sections, has_scan: true },
                scan: {
                    dimension: 1, is_relaxed: true, coordinate_count: 1, point_count: 5,
                    min_electronic_energy_hartree: -76.123456, max_electronic_energy_hartree: -75.987654,
                },
            }),
        })))
        page()
        await findLoaded("Frequency")
        fireEvent.click(screen.getByText("Scan trajectory"))
        const section = (await screen.findByText("Points")).closest("details") as HTMLElement
        expect(ddFor(section, "Min electronic energy (hartree)")).toBe("-76.123456")
        expect(ddFor(section, "Max electronic energy (hartree)")).toBe("-75.987654")
    })

    it("renders review history eagerly, without a disclosure", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                review_history: [
                    { status: "approved", note: null, reviewed_at: "2026-08-01T00:00:00", submission_ref: "sub_demo" },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const heading = screen.getByRole("heading", { name: "Review history" })
        expect(heading.closest("details")).toBeNull()
        expect(within(heading.closest("section") as HTMLElement).getByText("approved")).toBeVisible()
    })

    // Item 4: a genuinely never-reviewed calculation gets one line, not a
    // 3-cell table of "not recorded".
    it("renders 'Not yet reviewed' as one line, not a table, when the archive's synthesized row carries no date or note", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                review_history: [{ status: "not_reviewed", note: null, reviewed_at: null, submission_ref: null }],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const section = screen.getByRole("heading", { name: "Review history" }).closest("section") as HTMLElement
        expect(within(section).getByText("Not yet reviewed.")).toBeVisible()
        expect(within(section).queryByRole("table")).not.toBeInTheDocument()
        // The old duplicate "The current status is not reviewed..." sentence is gone.
        expect(within(section).queryByText(/The current status is/)).not.toBeInTheDocument()
    })

    it("still renders a real review-history table for an actually-reviewed calculation", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                review_history: [
                    { status: "approved", note: "Looks solid", reviewed_at: "2026-08-01T00:00:00", submission_ref: "sub_demo" },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const section = screen.getByRole("heading", { name: "Review history" }).closest("section") as HTMLElement
        expect(within(section).getByRole("table")).toBeVisible()
        expect(within(section).getByText("Looks solid")).toBeVisible()
    })

    // Item 7, design/foundations PR B: every table on this page moved from
    // the page-local `.stage-table` recipe to the shared `.data-table`
    // primitive (`design-system.css`), each inside a `.table-scroll`
    // wrapper. Mutation check: revert one table's className back to
    // `.stage-table` and this test fails.
    it("renders every table on this page as the shared .data-table primitive inside .table-scroll, never .stage-table", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                review_history: [
                    { status: "approved", note: "Looks solid", reviewed_at: "2026-08-01T00:00:00", submission_ref: "sub_demo" },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        fireEvent.click(screen.getByText("Parsed parameters"))
        await screen.findByText("scf_convergence")

        expect(document.querySelector(".stage-table")).toBeNull()
        const tables = Array.from(document.querySelectorAll("table"))
        expect(tables.length).toBeGreaterThan(0)
        for (const table of tables) {
            expect(table).toHaveClass("data-table")
            expect(table.closest(".table-scroll")).not.toBeNull()
        }
    })

    it("omits the Quality row for the default 'raw' value, but keeps the review-status pill visible", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")
        expect(screen.queryByText("Quality")).not.toBeInTheDocument()
        expect(screen.getByText("not reviewed", { selector: ".value-pill" })).toBeVisible()
    })

    // Item 6, design/foundations PR B: `.review-badge` is retired from this
    // page's own markup -- the review-status pill is now the ONE shared
    // `.value-pill` pill primitive, muted for "not reviewed" (an absent
    // verdict), accent for anything else. Mutation check: swap
    // `reviewPillClass`'s branches and this test fails on the muted half.
    it("uses the shared .value-pill primitive for the review-status pill, muted for 'not reviewed'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")
        const pill = screen.getByText("not reviewed", { selector: ".value-pill" })
        expect(pill).toHaveClass("value-pill")
        expect(pill).toHaveClass("value-pill--muted")
        expect(document.querySelector(".review-badge")).toBeNull()
    })

    it("shows the reviewed pill in the accent (non-muted) register once a calculation has actually been reviewed", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({ calculation: { ...mockRecord().calculation, review: { status: "approved", reviewed_at: "2026-08-01T00:00:00", reviewer_kind: "human" } } }),
        })))
        page()
        await findLoaded("Frequency")
        const pill = screen.getByText("approved", { selector: ".value-pill" })
        expect(pill).toHaveClass("value-pill")
        expect(pill).not.toHaveClass("value-pill--muted")
    })

    it("nests the review-status pill inside the heading block that contains the h1, not outside it", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        const h1 = await findLoaded("Frequency")
        const headingBlock = h1.closest(".record-header") as HTMLElement
        expect(headingBlock).not.toBeNull()
        const pill = within(headingBlock).getByText("not reviewed", { selector: ".value-pill" })
        expect(headingBlock.contains(pill)).toBe(true)
    })

    it("shows the Quality row for 'curated' and 'rejected'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({ calculation: { ...mockRecord().calculation, quality: "curated" } }),
        })))
        page()
        await findLoaded("Frequency")
        expect(screen.getByText("curated")).toBeVisible()
    })

    it("renders no submission row at all when the key is absent (anonymous caller)", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                provenance: {
                    has_result: true, converged: null,
                    geometry_validation_status: "not_present", scf_stability_status: "not_present",
                },
            }),
        })))
        page()
        await findLoaded("Frequency")
        expect(screen.queryByText("Submission ref")).not.toBeInTheDocument()
    })

    it("renders 'not recorded' (not an absent row) when the submission key is present but null (authenticated, no link)", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                provenance: {
                    has_result: true, converged: null,
                    geometry_validation_status: "not_present", scf_stability_status: "not_present",
                    submission_ref: null,
                },
            }),
        })))
        page()
        await findLoaded("Frequency")
        const context = screen.getByText("Submission ref").closest("dl") as HTMLElement
        expect(ddFor(context, "Submission ref")).toBe("not recorded")
    })

    // Item 2: the conformer block, when the archive links one.
    it("renders the conformer observation and group as links when the archive links a conformer observation", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                conformer: {
                    conformer_observation_ref: "cobs_demo",
                    conformer_group_ref: "cg_demo",
                    conformer_group_label: "basin-1",
                },
            }),
        })))
        page()
        await findLoaded("Frequency")
        expect(screen.getByRole("link", { name: "cobs_demo" })).toHaveAttribute("href", "/conformer-observations/cobs_demo")
        expect(screen.getByRole("link", { name: "basin-1" })).toHaveAttribute("href", "/conformer-groups/cg_demo")
    })

    it("renders no conformer row at all when the calculation has no linked observation", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord({ conformer: null }) })))
        page()
        await findLoaded("Frequency")
        expect(screen.queryByText("Conformer")).not.toBeInTheDocument()
    })

    // Item 1/2: the title, kicker, and stage strip all name the stage,
    // reading the SAME wording table (`domain/optimisationStage.ts`).
    // Owner complaint this replaces: "Coarse pass; refined by …" read as
    // if the coarse molecule had been refined BY the other one.
    it("names the coarse pass in the title/kicker and renders the stage strip, from a parent-side optimized_from edge", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "opt" },
                results: { kind: "opt", opt: { converged: true, n_steps: 2, final_energy_hartree: -78.1 }, sp: null, freq: null, scan: null, irc: null, path_search: null },
                dependencies: [
                    { role: "optimized_from", direction: "parent", parent_calculation_ref: "calc_freq_one", child_calculation_ref: "calc_fine_stage" },
                ],
            }),
        })))
        page()
        const h1 = await findLoaded("Coarse optimisation")
        expect(h1).toHaveTextContent("Coarse optimisation of CH3")
        expect(screen.getByText("Optimisation calculation · coarse pass")).toBeVisible()
        expect(screen.getByText("Optimisation stages")).toBeVisible()

        const strip = screen.getByTestId("opt-stage-strip")
        const coarseBox = within(strip).getByTestId("opt-stage-box-coarse")
        expect(within(coarseBox).getByText("Coarse optimisation")).toBeVisible()
        expect(coarseBox).toHaveClass("card--selected")
        expect(within(coarseBox).getByText("calc_freq_one")).toBeVisible()
        expect(within(coarseBox).queryByRole("link")).not.toBeInTheDocument()

        const fineBox = within(strip).getByTestId("opt-stage-box-fine")
        expect(fineBox).not.toHaveClass("card--selected")
        expect(within(fineBox).getByRole("link", { name: "calc_fine_stage" })).toHaveAttribute("href", "/calculations/calc_fine_stage")

        expect(screen.queryByText(/Coarse pass; refined by/)).not.toBeInTheDocument()
    })

    it("names the fine pass in the title/kicker and renders the stage strip, from a child-side optimized_from edge", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "opt" },
                results: { kind: "opt", opt: { converged: true, n_steps: 2, final_energy_hartree: -78.1 }, sp: null, freq: null, scan: null, irc: null, path_search: null },
                dependencies: [
                    { role: "optimized_from", direction: "child", parent_calculation_ref: "calc_coarse_stage", child_calculation_ref: "calc_freq_one" },
                ],
            }),
        })))
        page()
        const h1 = await findLoaded("Fine optimisation")
        expect(h1).toHaveTextContent("Fine optimisation of CH3")
        expect(screen.getByText("Optimisation calculation · fine pass")).toBeVisible()

        const strip = screen.getByTestId("opt-stage-strip")
        const fineBox = within(strip).getByTestId("opt-stage-box-fine")
        expect(fineBox).toHaveClass("card--selected")
        expect(within(fineBox).getByText("calc_freq_one")).toBeVisible()
        expect(within(fineBox).queryByRole("link")).not.toBeInTheDocument()

        const coarseBox = within(strip).getByTestId("opt-stage-box-coarse")
        expect(coarseBox).not.toHaveClass("card--selected")
        expect(within(coarseBox).getByRole("link", { name: "calc_coarse_stage" })).toHaveAttribute("href", "/calculations/calc_coarse_stage")

        expect(screen.queryByText(/Refinement of/)).not.toBeInTheDocument()
    })

    // Review finding (kept): an absent optimized_from edge is not
    // evidence of a single pass -- the old "Single-pass optimisation"
    // text asserted a stage the archive never actually reported.
    // "Refinement stage: not recorded" says exactly what the page knows.
    it("renders the muted 'not recorded' fact and no strip when no optimized_from edge exists on an opt calculation", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "opt" },
                results: { kind: "opt", opt: { converged: true, n_steps: 2, final_energy_hartree: -78.1 }, sp: null, freq: null, scan: null, irc: null, path_search: null },
                dependencies: [],
                available_sections: { ...mockRecord().available_sections, has_dependencies: false },
            }),
        })))
        page()
        const h1 = await findLoaded("Optimisation")
        expect(h1).toHaveTextContent("Optimisation of CH3")
        expect(screen.getByText("Optimisation calculation · deposited evidence")).toBeVisible()

        expect(screen.getByText("Refinement stage")).toBeVisible()
        const stageRow = screen.getByText("Refinement stage").closest("div") as HTMLElement
        const pill = within(stageRow).getByText("not recorded")
        expect(pill).toHaveClass("value-pill", "value-pill--muted")

        expect(screen.queryByTestId("opt-stage-strip")).not.toBeInTheDocument()
        expect(screen.queryByText("Optimisation stages")).not.toBeInTheDocument()
        expect(screen.queryByText("Single-pass optimisation")).not.toBeInTheDocument()
        expect(screen.queryByText("No refinement stage recorded")).not.toBeInTheDocument()
    })

    // Item 3: input == output collapses to one card, now framed as the
    // output (owner ask: "if we have no input geom for the coarse, then
    // we just say output geom").
    it("renders 'Output geometry' with the deposited-only note when the input and output geometry are the same stored ref", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                input_geometries: [{ geometry_ref: "geom_same", input_order: 1, output_order: null, role: null, natoms: 6, geom_hash: "x" }],
                output_geometries: [{ geometry_ref: "geom_same", input_order: null, output_order: 1, role: "final", natoms: 6, geom_hash: "x" }],
            }),
        })))
        page()
        await findLoaded("Frequency")
        expect(screen.getByRole("heading", { name: "Output geometry" })).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Input and output" })).not.toBeInTheDocument()
        expect(screen.getByText(
            "Only one geometry was deposited for this pass; the archive records it as the output. The starting geometry was not deposited.",
        )).toBeVisible()
        expect(screen.queryByText("Input and output are the same stored geometry.")).not.toBeInTheDocument()
        expect(screen.queryByText("extracted from the deposited input file")).not.toBeInTheDocument()
        expect(screen.getAllByRole("link", { name: "geom_same" })).toHaveLength(1)
    })

    // Post-review (#384 backfill): the sameGeometry branch used to say
    // "The starting geometry was not deposited" unconditionally -- false
    // once a coarse pass's extracted starting geometry dedupes to the
    // same row as its output (`input[0].source === "extracted_from_artifact"`).
    // That case gets the pill and a note that says a starting geometry
    // WAS deposited (extracted from the artifact), and happens to be
    // identical to the output.
    it("shows the extracted-from-artifact pill and note when the same-geometry input was extracted from a deposited artifact", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                input_geometries: [{ geometry_ref: "geom_same", input_order: 1, output_order: null, role: null, natoms: 6, geom_hash: "x", source: "extracted_from_artifact" }],
                output_geometries: [{ geometry_ref: "geom_same", input_order: null, output_order: 1, role: "final", natoms: 6, geom_hash: "x" }],
            }),
        })))
        page()
        await findLoaded("Frequency")
        expect(screen.getByRole("heading", { name: "Output geometry" })).toBeVisible()
        const pill = screen.getByText("extracted from the deposited input file")
        expect(pill).toHaveClass("value-pill", "value-pill--muted")
        expect(screen.getByText(
            "The starting geometry extracted from the deposited input file is identical to the output geometry.",
        )).toBeVisible()
        expect(screen.queryByText(
            "Only one geometry was deposited for this pass; the archive records it as the output. The starting geometry was not deposited.",
        )).not.toBeInTheDocument()
        expect(screen.getAllByRole("link", { name: "geom_same" })).toHaveLength(1)
    })

    it("keeps two separate cards when input and output geometries genuinely differ", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")
        expect(screen.queryByText("Input and output are the same stored geometry.")).not.toBeInTheDocument()
        expect(screen.getByRole("link", { name: "geom_input_one" })).toBeVisible()
        expect(screen.getByRole("link", { name: "geom_output_one" })).toBeVisible()
    })

    // Item 3: the "extracted from the deposited input file" pill -- a
    // concurrent backend PR's optional `source` field on the input-
    // geometry link summary.
    it("shows the 'extracted from the deposited input file' pill under an input geometry whose source is extracted_from_artifact", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                input_geometries: [
                    { geometry_ref: "geom_input_one", input_order: 1, output_order: null, role: null, natoms: 4, geom_hash: "abc", source: "extracted_from_artifact" },
                ],
            }),
        })))
        page()
        await findLoaded("Frequency")
        const inputHeading = screen.getByRole("heading", { name: "Input" })
        const inputSection = inputHeading.closest("div") as HTMLElement
        const pill = within(inputSection).getByText("extracted from the deposited input file")
        expect(pill).toHaveClass("value-pill", "value-pill--muted")
        // The Output list's own link renders no pill -- `source` on that
        // fixture's output row is absent.
        const outputHeading = screen.getByRole("heading", { name: "Output" })
        const outputSection = outputHeading.closest("div") as HTMLElement
        expect(within(outputSection).queryByText("extracted from the deposited input file")).not.toBeInTheDocument()
    })

    it("shows no extracted-from-artifact pill when the geometry link carries no source field", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")
        expect(screen.queryByText("extracted from the deposited input file")).not.toBeInTheDocument()
    })

    describe("headline energy", () => {
        function pageFor(ref: string) {
            return render(
                <MemoryRouter initialEntries={[`/calculations/${ref}`]}>
                    <Routes>
                        <Route path="/calculations/:calculationRef" element={<CalculationDetailPage />} />
                    </Routes>
                </MemoryRouter>,
            )
        }

        function spRecord(ref: string, electronicEnergyHartree: number) {
            return mockRecord({
                calculation: { ...mockRecord().calculation, calculation_ref: ref, type: "sp" },
                results: {
                    kind: "sp",
                    sp: { electronic_energy_hartree: electronicEnergyHartree, electronic_energy_uncertainty_hartree: null },
                    opt: null, freq: null, scan: null, irc: null, path_search: null,
                },
            })
        }

        it("promotes the calculation's OWN sp energy to a headline figure -- not another calculation's", async () => {
            server.use(
                http.get("/api/v1/scientific/calculations/calc_sp_a", () => HttpResponse.json({ record: spRecord("calc_sp_a", -76.100000) })),
                http.get("/api/v1/scientific/calculations/calc_sp_b", () => HttpResponse.json({ record: spRecord("calc_sp_b", -99.999999) })),
            )
            pageFor("calc_sp_b")
            await findLoaded("Single-point")
            expect(screen.getByTestId("energy-display-value")).toHaveTextContent("-99.999999 hartree")
        })

        it("carries the unit on the headline energy, checked for two different units", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: spRecord("calc_freq_one", -76.123456) })))
            page()
            await findLoaded("Single-point")
            const value = screen.getByTestId("energy-display-value")
            expect(value).toHaveTextContent("hartree")
            fireEvent.click(screen.getByRole("button", { name: "kJ/mol" }))
            expect(value).toHaveTextContent("kJ/mol")
            fireEvent.click(screen.getByRole("button", { name: "eV" }))
            expect(value).toHaveTextContent("eV")
        })

        it("round-trips the headline energy losslessly: switching away from hartree and back reproduces the exact original", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: spRecord("calc_freq_one", -76.1234567) })))
            page()
            await findLoaded("Single-point")
            const value = screen.getByTestId("energy-display-value")
            const original = value.textContent
            fireEvent.click(screen.getByRole("button", { name: "cm⁻¹" }))
            expect(value.textContent).not.toBe(original)
            fireEvent.click(screen.getByRole("button", { name: "hartree" }))
            expect(value.textContent).toBe(original)
        })

        it("renders no headline energy for a calculation type with no single answer (freq)", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
            page()
            await findLoaded("Frequency")
            expect(screen.queryByTestId("energy-display-value")).not.toBeInTheDocument()
        })

        // Item 4: the headline slot always renders for sp/opt -- even with
        // no result row at all, not only with a null value inside one.
        it("renders the headline slot with 'not recorded' for an opt calculation with NO result row at all", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({
                record: mockRecord({
                    calculation: { ...mockRecord().calculation, type: "opt" },
                    results: null,
                    provenance: { has_result: false, converged: null, geometry_validation_status: "not_present", scf_stability_status: "not_present" },
                }),
            })))
            page()
            await findLoaded("Optimisation")
            expect(screen.queryByTestId("energy-display-value")).not.toBeInTheDocument()
            const headline = document.querySelector(".calc-headline-energy") as HTMLElement
            expect(headline).not.toBeNull()
            expect(within(headline).getByText("Electronic energy at final geometry")).toBeVisible()
            expect(within(headline).getByText("not recorded")).toBeVisible()
        })

        it("keeps the label on the headline slot for an opt calculation with a result row but no recorded final energy", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({
                record: mockRecord({
                    calculation: { ...mockRecord().calculation, type: "opt" },
                    results: {
                        kind: "opt",
                        opt: { converged: null, n_steps: null, final_energy_hartree: null },
                        sp: null, freq: null, scan: null, irc: null, path_search: null,
                    },
                }),
            })))
            page()
            await findLoaded("Optimisation")
            expect(screen.queryByTestId("energy-display-value")).not.toBeInTheDocument()
            const headline = document.querySelector(".calc-headline-energy") as HTMLElement
            expect(within(headline).getByText("Electronic energy at final geometry")).toBeVisible()
            expect(within(headline).getByText("not recorded")).toBeVisible()
        })
    })

    it("demotes Related calculations and Review history below Result and Geometries", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await findLoaded("Frequency")
        const headingOrder = screen.getAllByRole("heading", { level: 2 }).map((el) => el.textContent)
        const dependenciesIndex = headingOrder.indexOf("Related calculations")
        const reviewIndex = headingOrder.indexOf("Review history")
        const resultsIndex = headingOrder.indexOf("Result")
        const geometriesIndex = headingOrder.indexOf("Geometries")
        expect(dependenciesIndex).toBeGreaterThan(resultsIndex)
        expect(dependenciesIndex).toBeGreaterThan(geometriesIndex)
        expect(reviewIndex).toBeGreaterThan(dependenciesIndex)
    })

    it("shows a specific not-found state for a 404", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({}, { status: 404 })))
        page()
        expect(await screen.findByRole("heading", { name: "Calculation not found" })).toBeVisible()
    })

    it("gives a wrong-handle-type 422 its own non-retryable state", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            code: "handle_type_mismatch",
            detail: "handle_type_mismatch: expected a calculation handle (prefix 'calc') but got prefix 'co'",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a calculation reference" })).toBeVisible()
        expect(screen.getByText(/expected a calculation handle/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    it("gives a malformed-ref 422 (code invalid_handle) its own non-retryable state, distinct from a wrong-prefix ref", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            code: "invalid_handle",
            detail: "invalid_handle: 'calc_' not a recognised calculation handle",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a calculation reference" })).toBeVisible()
        expect(screen.getByText(/not a recognised calculation handle/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    describe("table of contents applicability", () => {
        it("omits IRC, scan, path-search, vibrational-mode and geometry-validation entries for an sp calculation", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({
                record: mockRecord({
                    calculation: { ...mockRecord().calculation, type: "sp" },
                    results: {
                        kind: "sp",
                        sp: { electronic_energy_hartree: -76.1, electronic_energy_uncertainty_hartree: null },
                        opt: null, freq: null, scan: null, irc: null, path_search: null,
                    },
                    provenance: {
                        has_result: true, result_applicable: true,
                        converged: null, convergence_applicable: false,
                        geometry_validation_status: "not_present", geometry_validation_applicable: false,
                        scf_stability_status: "not_present",
                    },
                    available_sections: {
                        ...mockRecord().available_sections,
                        has_geometry_validation: false, geometry_validation_applicable: false,
                        has_constraints: false, constraints_applicable: false,
                        has_freq_modes: false, freq_modes_applicable: false,
                        has_scan: false, scan_applicable: false,
                        has_irc: false, irc_applicable: false,
                        has_path_search: false, path_search_applicable: false,
                    },
                }),
            })))
            page()
            await findLoaded("Single-point")

            const toc = await screen.findByRole("navigation", { name: "Sections on this page" })
            await waitFor(() => expect(within(toc).getAllByRole("link").length).toBeGreaterThan(3))
            const labels = within(toc).getAllByRole("link").map((link) => link.textContent)

            // Individual on-demand sections no longer register their own ToC
            // entries at all (item 10) -- they are folded under "Further
            // evidence", so the applicability rule is now observed through
            // the missing-sections note instead (covered above); the ToC
            // itself only carries the top-level sections.
            for (const heading of ["Result", "Geometries", "Related calculations", "Review history", "Further evidence"]) {
                expect(labels).toContain(heading)
            }
            for (const notASection of ["Scan trajectory", "IRC trajectory", "Path-search trajectory"]) {
                expect(labels).not.toContain(notASection)
            }
        })

        it("still renders imaginary-mode projections with its real content when a Hessian IS stored", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
            page()
            await findLoaded("Frequency")

            fireEvent.click(screen.getByText("Imaginary-mode projections"))
            const section = screen.getByText("Imaginary-mode projections").closest("details") as HTMLElement
            await within(section).findByText("Imaginary-mode projections loaded.")
            expect(ddFor(section, "Status")).toBe("determined")
        })

        // SCIENTIFIC ERROR regression guard (sweep finding, same class as
        // `ArrheniusChart.test.tsx`'s "s⁻¹"/"log₁₀ k" guards): `.data-table
        // th` (design-system.css) uppercases every header via `--type-
        // label-strong-transform` -- "cm-1" (wavenumber, lower-case "cm"
        // for centimetre) would read "CM-1" on both the Vibrational-modes
        // table and its Imaginary-mode-projections sibling. A `textContent`
        // assertion cannot see this -- the DOM text stays correctly-cased;
        // only the COMPUTED style differs -- so this asserts
        // `getComputedStyle`, per `vite.config.ts`'s `test.css: true`.
        it("computed text-transform is none on the Vibrational-modes table's 'Frequency (cm-1)' header", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
            page()
            await findLoaded("Frequency")

            fireEvent.click(screen.getByText("Vibrational modes"))
            const vibTable = await screen.findByRole("table", { name: "Vibrational modes" })
            expect(getComputedStyle(within(vibTable).getByRole("columnheader", { name: "Mode" })).textTransform).not.toBe("none")
            expect(getComputedStyle(within(vibTable).getByRole("columnheader", { name: "Frequency (cm-1)" })).textTransform).toBe("none")
        })

        it("computed text-transform is none on the Imaginary-mode-projections table's 'Frequency (cm-1)' header", async () => {
            const record = mockRecord()
            server.use(http.get(ENDPOINT, () => HttpResponse.json({
                record: {
                    ...record,
                    imaginary_mode_projections: {
                        ...(record as { imaginary_mode_projections: Record<string, unknown> }).imaginary_mode_projections,
                        modes: [{ mode_index: 1, frequency_cm1: -412.3, declared_disposition: "reaction_coordinate", determination: "reaction_coordinate", agreement: "agrees" }],
                    },
                },
            })))
            page()
            await findLoaded("Frequency")

            fireEvent.click(screen.getByText("Imaginary-mode projections"))
            const section = screen.getByText("Imaginary-mode projections").closest("details") as HTMLElement
            const projTable = await within(section).findByRole("table", { name: "Imaginary mode projections" })
            expect(getComputedStyle(within(projTable).getByRole("columnheader", { name: "Frequency (cm-1)" })).textTransform).toBe("none")
        })
    })
})
