import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import CalculationDetailPage from "./CalculationDetailPage"

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
                canonical_smiles: "[CH3]",
                inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
                charge: 0,
                multiplicity: 2,
                species_entry_kind: "minimum",
                electronic_state_kind: "ground",
            },
            transition_state_entry: null,
        },
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
    it("requests exactly the eager section tokens, in the documented order", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include")).toEqual([
                "results", "dependencies", "review", "input_geometries", "output_geometries",
            ])
            return HttpResponse.json({ record: mockRecord() })
        }))
        page()
        expect(await screen.findByRole("heading", { name: "Frequency calculation" })).toBeVisible()
    })

    it("renders breadcrumb and owner links, and keeps stable refs visible beside labels", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(breadcrumb).getByRole("link", { name: "Species" })).toHaveAttribute("href", "/species/spc_demo")
        expect(within(breadcrumb).getByRole("link", { name: "Species entry" }))
            .toHaveAttribute("href", "/species-entries/spe_demo")

        const owner = screen.getByRole("heading", { name: "Owner" }).closest("section") as HTMLElement
        // The link prefers the human label...
        expect(within(owner).getByRole("link", { name: "ground state" })).toHaveAttribute("href", "/species-entries/spe_demo")
        // ...but the stable ref stays visible on its own line regardless (never `label ?? ref`).
        expect(within(owner).getByText("spe_demo", { selector: "dd" })).toBeVisible()

        // The calculation's own ref is always shown raw (it carries no label).
        expect(screen.getByText("calc_freq_one", { selector: "dd" })).toBeVisible()
    })

    // Item 4 of the design brief: `species_entry_label` is measured null on
    // every entry sampled off the live archive's browse endpoint, so the
    // link above ALREADY falls back to showing the ref -- a separate
    // "Species entry ref" row underneath it would repeat that same string
    // verbatim. Two fixtures (this one has NO label; the test above has
    // one), because a single fixture cannot tell "always shows the row"
    // apart from "shows it only when it says something new" -- the bug IS
    // the untested branch.
    it("omits the duplicate 'Species entry ref' row when the entry has no label (the link already shows the ref)", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                owner: {
                    kind: "species_entry",
                    species_entry: {
                        species_ref: "spc_demo", species_entry_ref: "spe_demo",
                        species_entry_label: null,
                        canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
                        charge: 0, multiplicity: 2,
                        species_entry_kind: "minimum", electronic_state_kind: "ground",
                    },
                    transition_state_entry: null,
                },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const owner = screen.getByRole("heading", { name: "Owner" }).closest("section") as HTMLElement
        // The link now shows the ref itself, since there is no label to prefer.
        expect(within(owner).getByRole("link", { name: "spe_demo" })).toHaveAttribute("href", "/species-entries/spe_demo")
        // No second row repeating it -- positively confirmed there is
        // exactly one "spe_demo" text in the owner card, not zero (which
        // would mean the ref vanished) and not two (the duplicate).
        expect(within(owner).getAllByText("spe_demo")).toHaveLength(1)
        expect(within(owner).queryByText("Species entry ref")).not.toBeInTheDocument()
    })

    it("surfaces level_of_theory_ref, dispersion and solvent — the fields that actually distinguish two rows with the same compact label", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const context = screen.getByText("Calculation ref").closest(".record-context") as HTMLElement
        expect(within(context).getByText("Level of theory ref").closest("div")).not.toBeNull()
        const lotRefRow = within(context).getByText("Level of theory ref").closest("div") as HTMLElement
        expect(within(lotRefRow).getByText("lot_1")).toBeVisible()

        const dispersionRow = within(context).getByText("Dispersion").closest("div") as HTMLElement
        expect(within(dispersionRow).getByText("D3BJ")).toBeVisible()

        const solventRow = within(context).getByText("Solvent").closest("div") as HTMLElement
        expect(within(solventRow).getByText("water(CPCM)")).toBeVisible()

        const softwareRefRow = within(context).getByText("Software release ref").closest("div") as HTMLElement
        expect(within(softwareRefRow).getByText("srel_1")).toBeVisible()

        const workflowRefRow = within(context).getByText("Workflow tool release ref").closest("div") as HTMLElement
        expect(within(workflowRefRow).getByText("wfr_1")).toBeVisible()
    })

    it("does not stutter the software/workflow-tool name when the version already opens with it", async () => {
        // Regression test for the exact live defect the brief names: naive
        // `${name} ${version}` concatenation on this fixture's
        // `version: "Gaussian 16, Revision C.02"` (which already opens with
        // "Gaussian") used to render "Gaussian Gaussian 16, Revision C.02".
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const context = screen.getByText("Calculation ref").closest(".record-context") as HTMLElement
        const softwareRow = within(context).getByText("Software", { selector: "dt" }).closest("div") as HTMLElement
        expect(within(softwareRow).getByText("Gaussian 16, Revision C.02")).toBeVisible()
        expect(within(softwareRow).queryByText(/Gaussian Gaussian/)).not.toBeInTheDocument()

        // workflow_tool_release's version ("1.1.0") does NOT already open
        // with its name ("ARC"), so this exercises the concatenating branch.
        const workflowRow = within(context).getByText("Workflow tool", { selector: "dt" }).closest("div") as HTMLElement
        expect(within(workflowRow).getByText("ARC 1.1.0")).toBeVisible()
    })

    it("renders the owner species' charge and multiplicity through the shared chemistry-format rules, not raw numbers", async () => {
        // mockRecord's owner species carries charge: 0, multiplicity: 2 —
        // a raw render would show "0 / 2"; the shared rules render the
        // charge as a signed quantity ("0" for neutral) and the
        // multiplicity paired with its spin word ("doublet (2)").
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const ownerSection = screen.getByRole("heading", { name: "Owner" }).closest("section") as HTMLElement
        const chargeRow = within(ownerSection).getByText("Charge / multiplicity").closest("div") as HTMLElement
        // Charge and multiplicity each render as their own pill (item 5 of
        // the design brief) rather than one joined "0 / doublet (2)"
        // string -- the two values are still both there, just as two
        // adjacent elements under the one "Charge / multiplicity" label.
        const pills = within(chargeRow).getAllByText((_, el) => el?.classList.contains("value-pill") === true)
        expect(pills.map((el) => el.textContent)).toEqual(["0", "doublet (2)"])
    })

    it("renders the literature citation and its own stable ref, never dropping it silently", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const context = screen.getByText("Calculation ref").closest(".record-context") as HTMLElement
        const literatureRow = within(context).getByText("Literature", { selector: "dt" }).closest("div") as HTMLElement
        expect(within(literatureRow).getByText(/A study of methyl radicals/)).toBeVisible()
        const literatureRefRow = within(context).getByText("Literature ref").closest("div") as HTMLElement
        expect(within(literatureRefRow).getByText("lit_1")).toBeVisible()
    })

    it("keeps input-geometry, output-geometry and dependency-edge counts in their own metric", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const inputMetric = screen.getByText("Input geometries").closest(".metric") as HTMLElement
        const outputMetric = screen.getByText("Output geometries").closest(".metric") as HTMLElement
        const depMetric = screen.getByText("Dependency edges").closest(".metric") as HTMLElement
        expect(within(inputMetric).getByText("1")).toBeVisible()
        expect(within(outputMetric).getByText("2")).toBeVisible()
        expect(within(depMetric).getByText("1")).toBeVisible()
    })

    it("draws a dependency edge only from the dependencies payload, never from calculation type", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const depSection = screen.getByRole("heading", { name: "Dependency graph" }).closest("section") as HTMLElement
        expect(within(depSection).getByText("depends on")).toBeVisible()
        expect(within(depSection).getByText("frequency on")).toBeVisible()
        expect(within(depSection).getByRole("link", { name: "calc_opt_parent" }))
            .toHaveAttribute("href", "/calculations/calc_opt_parent")
    })

    it("shows no dependency edge when the archive returns none, even though the calculation is a freq stage", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                dependencies: [],
                available_sections: { ...mockRecord().available_sections, has_dependencies: false },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const depSection = screen.getByRole("heading", { name: "Dependency graph" }).closest("section") as HTMLElement
        expect(within(depSection).getByText("No dependency edges are recorded for this calculation.")).toBeVisible()
        expect(within(depSection).queryByRole("link")).not.toBeInTheDocument()
    })

    it("flags a contradiction when the archive marks dependency evidence present but returns none", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord({ dependencies: [] }) })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        expect(await screen.findByText(
            (_, element) => element?.tagName === "P"
                && (element.textContent ?? "").includes("The archive marks this calculation as having recorded evidence here"),
        )).toBeVisible()
    })

    it("treats an absent dependencies key as 'not requested', distinct from a genuinely empty one", async () => {
        // This client's eager include set is fixed, so this key should
        // never actually be absent in practice — but the wire type allows
        // it, and a future response that dropped the key must not be
        // reported as "the archive was asked and found nothing".
        const withoutKey = mockRecord()
        delete (withoutKey as Record<string, unknown>).dependencies
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: withoutKey })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const depSection = screen.getByRole("heading", { name: "Dependency graph" }).closest("section") as HTMLElement
        expect(within(depSection).getByText("This section was not requested for this view.")).toBeVisible()
        expect(within(depSection).queryByText("No dependency edges are recorded for this calculation."))
            .not.toBeInTheDocument()
    })

    it("binds each dependency row's relationship, role and related ref together — not from the first row, and not inferred from direction alone", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                // Deliberately NOT in role-alphabetical order (the live
                // archive happens to pre-sort its own edges by role, so a
                // fixture that mirrored that order verbatim would make a
                // client-side "sort by role" mutation unobservable — see
                // the ordering assertion below). Order here is the one
                // thing under test alongside per-row binding: two edges
                // share the "parent" direction but have different roles,
                // so no function of direction alone can produce the right
                // role for both.
                dependencies: [
                    {
                        role: "single_point_on", direction: "parent",
                        parent_calculation_ref: "calc_freq_one", child_calculation_ref: "calc_osrf4pnfcesq6s6somn7nr5hly",
                    },
                    {
                        role: "freq_on", direction: "parent",
                        parent_calculation_ref: "calc_freq_one", child_calculation_ref: "calc_rypxkxvsku5x2nk6sqbhhmfcla",
                    },
                    {
                        role: "optimized_from", direction: "child",
                        parent_calculation_ref: "calc_htgb7s5nakuw52eqhcxpvilpoq", child_calculation_ref: "calc_freq_one",
                    },
                ],
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const depSection = screen.getByRole("heading", { name: "Dependency graph" }).closest("section") as HTMLElement

        const freqRow = within(depSection).getByRole("link", { name: "calc_rypxkxvsku5x2nk6sqbhhmfcla" }).closest("tr") as HTMLElement
        expect(within(freqRow).getByText("feeds into")).toBeVisible()
        expect(within(freqRow).getByText("frequency on")).toBeVisible()
        expect(within(freqRow).queryByText("single point on")).not.toBeInTheDocument()
        expect(within(freqRow).queryByText("optimized from")).not.toBeInTheDocument()

        const optimizedFromRow = within(depSection).getByRole("link", { name: "calc_htgb7s5nakuw52eqhcxpvilpoq" }).closest("tr") as HTMLElement
        expect(within(optimizedFromRow).getByText("depends on")).toBeVisible()
        expect(within(optimizedFromRow).getByText("optimized from")).toBeVisible()
        expect(within(optimizedFromRow).queryByText("frequency on")).not.toBeInTheDocument()

        const spRow = within(depSection).getByRole("link", { name: "calc_osrf4pnfcesq6s6somn7nr5hly" }).closest("tr") as HTMLElement
        expect(within(spRow).getByText("feeds into")).toBeVisible()
        expect(within(spRow).getByText("single point on")).toBeVisible()
        // The freq row and the sp row share "feeds into" (both "parent"
        // direction) but must not share a role: this is what a
        // direction-only inference of role cannot produce.
        expect(within(spRow).queryByText("frequency on")).not.toBeInTheDocument()

        // Row order is read verbatim from the payload, not re-derived by
        // sorting on role (or on anything else) — the slice rule again:
        // no relationship, including ordering, may be fabricated
        // client-side. Header row excluded via slice(1).
        const rows = within(depSection).getAllByRole("row").slice(1)
        const relatedRefs = rows.map((row) => within(row).getByRole("link").textContent)
        expect(relatedRefs).toEqual([
            "calc_osrf4pnfcesq6s6somn7nr5hly", // single_point_on, first in the payload
            "calc_rypxkxvsku5x2nk6sqbhhmfcla", // freq_on, second in the payload
            "calc_htgb7s5nakuw52eqhcxpvilpoq", // optimized_from, third in the payload
        ])
    })

    it("reports a check as recorded/absent, never as a pass/fail verdict, on the summary card", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const summary = screen.getByLabelText("Calculation evidence summary")
        const coverage = summary.querySelector(".coverage-card") as HTMLElement
        // Scoped to the checklist itself — the coverage card's caveat
        // sentence legitimately contains the word "passed" ("not that it
        // passed"), so asserting over the whole card would pass whether or
        // not the checklist itself ever named a pass/fail verdict.
        const checklist = coverage.querySelector(".coverage-checklist") as HTMLElement
        expect(ddFor(checklist, "Geometry validation")).toBe("absent")
        expect(within(checklist).queryByText(/passed/i)).not.toBeInTheDocument()
        expect(within(checklist).queryByText(/fail/i)).not.toBeInTheDocument()
    })

    // The house defect (a fixture whose shape excludes the bug) applied
    // directly: a single fixture asserting one row in isolation would pass
    // even if EVERY row rendered "absent" regardless of its own state, or
    // if all four checks collapsed onto one shared row. Each of the four
    // checks is asserted as its own row with its own label, appearing
    // exactly once.
    it("renders each of the four evidence checks as its own labelled row", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                provenance: {
                    has_result: true,
                    converged: true,
                    geometry_validation_status: "not_present",
                    scf_stability_status: "present",
                    submission_ref: "sub_demo",
                },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const checklist = document.querySelector(".coverage-checklist") as HTMLElement
        expect(checklist.querySelectorAll("dt")).toHaveLength(4)
        for (const label of ["Result", "Geometry validation", "SCF stability", "Convergence"]) {
            expect(within(checklist).getAllByText(label)).toHaveLength(1)
        }
        expect(ddFor(checklist, "Result")).toBe("recorded")
        expect(ddFor(checklist, "Geometry validation")).toBe("absent")
        expect(ddFor(checklist, "SCF stability")).toBe("recorded")
        expect(ddFor(checklist, "Convergence")).toBe("converged")
    })

    // The exact assertion the brief calls out: `converged: false` and
    // `converged: null` must render DIFFERENT text -- a missing convergence
    // check is not the same claim as a convergence check that ran and
    // failed. Asserting each value in isolation would pass even if both
    // mapped to the same string; this asserts the DIFFERENCE directly.
    it("keeps 'not converged' (an outcome) and the absence wording (no data) visibly distinct", async () => {
        // Two independent renders (not a shared server handler toggle) so
        // each is unambiguous about which `converged` value produced it.
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                provenance: {
                    has_result: true,
                    converged: false,
                    geometry_validation_status: "not_present",
                    scf_stability_status: "not_present",
                },
            }),
        })))
        const { unmount } = page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const notConvergedText = ddFor(document.querySelector(".coverage-checklist") as HTMLElement, "Convergence")
        unmount()
        cleanup()

        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                provenance: {
                    has_result: true,
                    converged: null,
                    geometry_validation_status: "not_present",
                    scf_stability_status: "not_present",
                },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const absentText = ddFor(document.querySelector(".coverage-checklist") as HTMLElement, "Convergence")

        expect(notConvergedText).toBe("not converged")
        expect(absentText).toBe("absent")
        expect(notConvergedText).not.toBe(absentText)
    })

    // Fix under test: "not applicable" (this calculation's TYPE cannot
    // have the check) must never collapse onto "absent" (this type can
    // have it, and doesn't). A single-type fixture cannot catch that
    // collapse -- an `sp` calculation and an `opt` calculation are
    // rendered from two independent fixtures and asserted to differ, the
    // same house-defect-avoidance shape as the "not converged" test
    // above.
    it("reads convergence and geometry validation as 'not applicable' for an sp calculation, not 'absent'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "sp" },
                provenance: {
                    has_result: true,
                    result_applicable: true,
                    converged: null,
                    convergence_applicable: false,
                    geometry_validation_status: "not_present",
                    geometry_validation_applicable: false,
                    scf_stability_status: "not_present",
                },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Single-point calculation" })
        const checklist = document.querySelector(".coverage-checklist") as HTMLElement
        expect(ddFor(checklist, "Convergence")).toBe("not applicable")
        expect(ddFor(checklist, "Geometry validation")).toBe("not applicable")
    })

    it("reads convergence and geometry validation as 'absent'/'recorded' for an opt calculation -- the sp reading above must differ", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "opt" },
                provenance: {
                    has_result: true,
                    result_applicable: true,
                    converged: null,
                    convergence_applicable: true,
                    geometry_validation_status: "passed",
                    geometry_validation_applicable: true,
                    scf_stability_status: "not_present",
                },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Optimisation calculation" })
        const checklist = document.querySelector(".coverage-checklist") as HTMLElement
        // Same two rows the sp fixture above reads as "not applicable" --
        // an opt calculation genuinely can have this evidence, so an
        // unrecorded convergence check is "absent", not "not applicable".
        expect(ddFor(checklist, "Convergence")).toBe("absent")
        expect(ddFor(checklist, "Geometry validation")).toBe("recorded")
    })

    it("keeps not-applicable, absent, and 'not converged' three-way distinct for Convergence", async () => {
        // sp: cannot have a convergence flag at all.
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "sp" },
                provenance: {
                    has_result: true, converged: null, convergence_applicable: false,
                    geometry_validation_status: "not_present", scf_stability_status: "not_present",
                },
            }),
        })))
        const notApplicableRender = page()
        await screen.findByRole("heading", { name: "Single-point calculation" })
        const notApplicableText = ddFor(document.querySelector(".coverage-checklist") as HTMLElement, "Convergence")
        notApplicableRender.unmount()
        cleanup()

        // opt, no result row: applicable, nothing recorded yet.
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "opt" },
                provenance: {
                    has_result: false, converged: null, convergence_applicable: true,
                    geometry_validation_status: "not_present", scf_stability_status: "not_present",
                },
            }),
        })))
        const absentRender = page()
        await screen.findByRole("heading", { name: "Optimisation calculation" })
        const absentText = ddFor(document.querySelector(".coverage-checklist") as HTMLElement, "Convergence")
        absentRender.unmount()
        cleanup()

        // opt, result row present, the optimisation ran and failed.
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                calculation: { ...mockRecord().calculation, type: "opt" },
                provenance: {
                    has_result: true, converged: false, convergence_applicable: true,
                    geometry_validation_status: "not_present", scf_stability_status: "not_present",
                },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Optimisation calculation" })
        const notConvergedText = ddFor(document.querySelector(".coverage-checklist") as HTMLElement, "Convergence")

        expect(notApplicableText).toBe("not applicable")
        expect(absentText).toBe("absent")
        expect(notConvergedText).toBe("not converged")
        // Pairwise distinct -- three different readings, not just three
        // different code paths that happen to print the same string.
        expect(new Set([notApplicableText, absentText, notConvergedText]).size).toBe(3)
    })

    it("renders an available on-demand section as idle (not fetched, not empty) until it is opened", async () => {
        let requestCount = 0
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            requestCount += 1
            if (includes.includes("parameters")) {
                return HttpResponse.json({ record: mockRecord() })
            }
            return HttpResponse.json({ record: mockRecord() })
        }))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const section = screen.getByRole("heading", { name: "Parsed parameters" }).closest("details") as HTMLDetailsElement
        expect(section).not.toBeNull()
        expect(section.open).toBe(false)
        // Not yet opened: idle, not the "no rows" empty state. The idle
        // text is present but native <details> semantics correctly hide it
        // (and everything else but <summary>) while closed, so this checks
        // presence rather than visibility.
        expect(within(section).getByText("Expand to load this section from the archive.")).toBeInTheDocument()
        expect(within(section).queryByText(/The archive returned no parameter rows/)).not.toBeInTheDocument()
        expect(requestCount).toBe(1) // only the eager fetch so far

        fireEvent.click(screen.getByRole("heading", { name: "Parsed parameters" }))
        expect(await within(section).findByText("scf_convergence")).toBeVisible()
        expect(requestCount).toBe(2)

        // Re-toggling does not re-request. jsdom queues the native
        // `toggle` event as a task rather than firing it synchronously
        // with the click, so an assertion taken immediately after the two
        // `fireEvent.click` calls below reads the state from *before*
        // either re-toggle is actually processed — it would pass whether
        // or not the fetch-once guard in `useCalculationSection` does
        // anything at all. Flushing past that queued task first (an
        // explicit `waitFor(() => expect(...).toBe(2))` on `requestCount`
        // would pass trivially, on the very first poll, since 2 is already
        // the count from the initial open — it never actually waits for
        // the re-toggles) makes this assert something real.
        fireEvent.click(screen.getByRole("heading", { name: "Parsed parameters" }))
        fireEvent.click(screen.getByRole("heading", { name: "Parsed parameters" }))
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
        await screen.findByRole("heading", { name: "Frequency calculation" })
        fireEvent.click(screen.getByRole("heading", { name: "Geometry validation" }))
        // The caveat sentence's text is split across several <code>
        // boundaries, so this matches on the paragraph's combined
        // textContent rather than a substring that would need to fall
        // entirely within one text node.
        const caveat = await screen.findByText(
            (_, element) => element?.tagName === "P"
                && (element.textContent ?? "").includes("is_isomorphic")
                && (element.textContent ?? "").includes("formula_matches")
                && (element.textContent ?? "").includes("same stored verdict under two names"),
        )
        expect(caveat).toBeVisible()
        // is_isomorphic is shown, not silently dropped, alongside the
        // preferred formula_matches name for the same value.
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
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const section = screen.getByRole("heading", { name: "Parsed parameters" }).closest("details") as HTMLElement
        fireEvent.click(screen.getByRole("heading", { name: "Parsed parameters" }))

        // The live region is `role="status"` for every state (idle,
        // loading, error, ready alike — see LazySection's docstring for
        // why an assertive `role="alert"` nested in it was dropped), so
        // that same node exists from the very first render (currently
        // showing "Loading…") — `findByRole("status")` would resolve
        // immediately against that, before the fetch settles. Waiting for
        // the error message's own text is what actually waits for the
        // fetch to fail.
        const status = await within(section).findByText("internal error")
        expect(status.getAttribute("role")).toBe("status")
        // A transient outage must never read the same as "the archive was
        // asked and has nothing to say" — the single worst outcome on this
        // page, since it reads as the calculation carrying no evidence
        // rather than as a request that failed.
        expect(within(section).queryByText(/The archive returned no parameter rows/)).not.toBeInTheDocument()
        expect(within(section).queryByText(/no execution parameters were parsed/i)).not.toBeInTheDocument()
        // Nor may it read as success.
        expect(within(section).queryByText("Parsed parameters loaded.")).not.toBeInTheDocument()
    })

    it("renders a section available_sections marks empty as a static line, with no disclosure to open", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        // This fixture is a `freq` calculation: has_scan is false.
        const heading = screen.getByRole("heading", { name: "Scan trajectory" })
        expect(heading.closest("details")).toBeNull()
        const section = heading.closest("section") as HTMLElement
        expect(within(section).getByText("This calculation has no scan result.")).toBeVisible()
    })

    it("gates imaginary-mode projections on has_hessian, not on a dedicated available flag", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({ available_sections: { ...mockRecord().available_sections, has_hessian: false } }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const heading = screen.getByRole("heading", { name: "Imaginary-mode projections" })
        expect(heading.closest("details")).toBeNull()
        expect(screen.getByText(/Not determinable — no Hessian is stored/)).toBeVisible()
    })

    it("surfaces the provenance pointers on the on-demand sections, not just their headline fields", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        // SCF stability: it came from a different calculation than this one.
        fireEvent.click(screen.getByRole("heading", { name: "SCF stability" }))
        const scfSection = screen.getByRole("heading", { name: "SCF stability" }).closest("details") as HTMLElement
        expect(await within(scfSection).findByRole("link", { name: "calc_stability_source" }))
            .toHaveAttribute("href", "/calculations/calc_stability_source")

        // Geometry validation: input/output geometry refs, not just the verdict.
        fireEvent.click(screen.getByRole("heading", { name: "Geometry validation" }))
        const gvSection = screen.getByRole("heading", { name: "Geometry validation" }).closest("details") as HTMLElement
        expect(await within(gvSection).findByRole("link", { name: "geom_input_one" }))
            .toHaveAttribute("href", "/geometries/geom_input_one")
        expect(within(gvSection).getByRole("link", { name: "geom_output_one" }))
            .toHaveAttribute("href", "/geometries/geom_output_one")

        // Artifacts: the sha256 is the artifact's identity.
        fireEvent.click(screen.getByRole("heading", { name: "Artifacts" }))
        const artifactSection = screen.getByRole("heading", { name: "Artifacts" }).closest("details") as HTMLElement
        expect(await within(artifactSection).findByText("a".repeat(64))).toBeVisible()

        // Energy corrections: the scheme ref, not just its human-readable name.
        fireEvent.click(screen.getByRole("heading", { name: "Energy corrections" }))
        const ecSection = screen.getByRole("heading", { name: "Energy corrections" }).closest("details") as HTMLElement
        expect(await within(ecSection).findByText("ecs_1")).toBeVisible()
    })

    it("marks a result whose shape this view does not recognise, rather than rendering an empty result section", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({ results: { kind: "sp", sp: null, opt: null, freq: null, scan: null, irc: null, path_search: null } }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        // `kind: "sp"` with `sp: null` is a shape this page has no branch
        // for cleanly — the prior behaviour rendered nothing at all here
        // (a heading and prose over an empty <dl>), which reads as neither
        // "recorded" nor "not recorded".
        const notice = within(resultsSection).getByRole("alert")
        expect(notice).toHaveTextContent(/not recognised/)
        expect(resultsSection.querySelector(".kv-list")).toBeNull()
    })

    it("formats an sp result's electronic energy at its own 6dp spec, not the 4dp frequency-scale-factor spec", async () => {
        // -76.1234567 rounds to "-76.123457" at 6dp but "-76.1235" at 4dp --
        // a table-row swap (using `statmech_frequency_scale_factor` here
        // instead of `calculation_electronic_energy_hartree`) produces a
        // visibly different string.
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
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        expect(ddFor(resultsSection, "Electronic energy (hartree)")).toBe("-76.123457")
    })

    it("formats an opt result's final energy at the same 6dp spec as an sp result's electronic energy", async () => {
        // Regression test for the brief's defect #1 surviving in the file
        // that fixed it: the opt branch used to pass `final_energy_hartree`
        // through raw.
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                results: {
                    kind: "opt",
                    opt: { converged: true, n_steps: 12, final_energy_hartree: -76.1234567 },
                    sp: null, freq: null, scan: null, irc: null, path_search: null,
                },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const resultsSection = screen.getByRole("heading", { name: "Result" }).closest("section") as HTMLElement
        expect(ddFor(resultsSection, "Final energy (hartree)")).toBe("-76.123457")
    })

    it("binds a scan's min and max electronic energy to their own labelled row — never swapped", async () => {
        // Distinct 6dp-rounded values in each direction, so a min/max swap
        // (or a spec-table row swap) is observable from either row alone.
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
        await screen.findByRole("heading", { name: "Frequency calculation" })
        fireEvent.click(screen.getByRole("heading", { name: "Scan trajectory" }))
        const section = (await screen.findByText("Points")).closest(".ledger-section, details") as HTMLElement
        expect(ddFor(section, "Min electronic energy (hartree)")).toBe("-76.123456")
        expect(ddFor(section, "Max electronic energy (hartree)")).toBe("-75.987654")
    })

    it("renders review history eagerly, without a disclosure", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const heading = screen.getByRole("heading", { name: "Review history" })
        expect(heading.closest("details")).toBeNull()
        expect(within(heading.closest("section") as HTMLElement).getByText("not reviewed")).toBeVisible()
    })

    it("renders a transition-state owner without a link, since that route does not exist yet", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                owner: {
                    kind: "transition_state_entry",
                    species_entry: null,
                    transition_state_entry: {
                        transition_state_ref: "ts_demo",
                        transition_state_entry_ref: "tse_demo",
                        label: "saddle point",
                        charge: 0,
                        multiplicity: 1,
                        status: "candidate",
                        reaction_entry_ref: null,
                    },
                },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const owner = screen.getByRole("heading", { name: "Owner" }).closest("section") as HTMLElement
        expect(within(owner).getByText("tse_demo", { selector: "dd" })).toBeVisible()
        expect(within(owner).queryByRole("link")).not.toBeInTheDocument()
        expect(screen.queryByRole("link", { name: "Species entry" })).not.toBeInTheDocument()
    })

    it("gives the species-entry owner heading its own distinct id", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        const heading = await screen.findByRole("heading", { name: "Owner" })
        expect(heading.id).toBe("owner-heading-species-entry")
        expect(document.getElementById("owner-heading-transition-state-entry")).toBeNull()
    })

    it("gives the transition-state owner heading a DIFFERENT id from the species-entry one, on the same page component", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                owner: {
                    kind: "transition_state_entry",
                    species_entry: null,
                    transition_state_entry: {
                        transition_state_ref: "ts_demo", transition_state_entry_ref: "tse_demo",
                        label: "saddle point", charge: 0, multiplicity: 1, status: "candidate", reaction_entry_ref: null,
                    },
                },
            }),
        })))
        page()
        const heading = await screen.findByRole("heading", { name: "Owner" })
        expect(heading.id).toBe("owner-heading-transition-state-entry")
        expect(document.getElementById("owner-heading-species-entry")).toBeNull()
    })

    it("renders the owner's classification (kind, state) as labelled rows on the owner card -- no pill row duplicating them", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const owner = screen.getByRole("heading", { name: "Owner" }).closest("section") as HTMLElement
        expect(ddFor(owner, "Entry kind")).toBe("minimum")
        expect(ddFor(owner, "Electronic state")).toBe("ground")
        // The bug this replaces: a `.record-facet-chips` pill row repeating
        // the same two facts a second time, right below this same card.
        expect(document.querySelector(".record-facet-chips")).not.toBeInTheDocument()
    })

    // Item 5: pills are for bounded-vocabulary categorical values only.
    // Both directions asserted -- a rule that pilled EVERY value would pass
    // a test that only checked the categorical side.
    it("renders a categorical value as a pill and an identifier as plain text -- both directions", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const owner = screen.getByRole("heading", { name: "Owner" }).closest("section") as HTMLElement

        // Categorical: entry kind, a bounded species_entry_kind vocabulary value.
        const entryKindDd = within(owner).getByText("Entry kind").closest("div")!.querySelector("dd")!
        expect(entryKindDd.querySelector(".value-pill")).not.toBeNull()
        expect(entryKindDd.querySelector(".value-pill")?.textContent).toBe("minimum")

        // Identifier: the species entry ref -- a stable, copyable, arbitrary-length
        // handle, never a bounded vocabulary value, and never a pill.
        const speciesEntryDd = within(owner).getByText("Species entry").closest("div")!.querySelector("dd")!
        expect(speciesEntryDd.querySelector(".value-pill")).toBeNull()
    })

    // Item 7: `quality` and `record_review.status` are two separate
    // mechanisms that only happen to agree on the live archive today
    // (measured: all 572 calculations are `(not_reviewed, raw)`) because
    // neither has been used yet. `raw` is the column's own server default,
    // so showing it unconditionally distinguishes nothing -- but `curated`
    // and `rejected` are load-bearing (they change filtering/trust scoring
    // elsewhere) and must stay visible. Three fixtures, since the archive
    // today only ever produces one of the three values.
    it("omits the Quality row for the default 'raw' value, but keeps the review-status badge visible", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const context = screen.getByText("Calculation ref").closest(".record-context") as HTMLElement
        expect(within(context).queryByText("Quality")).not.toBeInTheDocument()
        // Positively asserted so this test cannot pass by the whole record
        // header failing to render: the review-status badge is still there.
        expect(screen.getByText("not reviewed", { selector: ".review-badge" })).toBeVisible()
    })

    it("shows the Quality row for 'curated'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({ calculation: { ...mockRecord().calculation, quality: "curated" } }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const context = screen.getByText("Calculation ref").closest(".record-context") as HTMLElement
        expect(ddFor(context, "Quality")).toBe("curated")
    })

    it("shows the Quality row for 'rejected' -- the more consequential of the two non-default values", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({ calculation: { ...mockRecord().calculation, quality: "rejected" } }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const context = screen.getByText("Calculation ref").closest(".record-context") as HTMLElement
        expect(ddFor(context, "Quality")).toBe("rejected")
    })

    it("renders no submission row at all when the key is absent (anonymous caller)", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: mockRecord({
                provenance: {
                    has_result: true, converged: null,
                    geometry_validation_status: "not_present", scf_stability_status: "not_present",
                    // No `submission_ref` key at all -- the anonymous shape.
                },
            }),
        })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
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
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const context = screen.getByText("Submission ref").closest("dl") as HTMLElement
        expect(ddFor(context, "Submission ref")).toBe("not recorded")
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
            // Two calculations, two DIFFERENT energies. A page reading the
            // wrong one (a stale fetch, `records[0]`-style bug) is only
            // observable because the two values differ.
            server.use(
                http.get("/api/v1/scientific/calculations/calc_sp_a", () => HttpResponse.json({ record: spRecord("calc_sp_a", -76.100000) })),
                http.get("/api/v1/scientific/calculations/calc_sp_b", () => HttpResponse.json({ record: spRecord("calc_sp_b", -99.999999) })),
            )
            pageFor("calc_sp_b")
            await screen.findByRole("heading", { name: "Single-point calculation" })
            expect(screen.getByTestId("energy-display-value")).toHaveTextContent("-99.999999 hartree")
        })

        it("carries the unit on the headline energy, checked for two different units", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: spRecord("calc_freq_one", -76.123456) })))
            page()
            await screen.findByRole("heading", { name: "Single-point calculation" })
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
            await screen.findByRole("heading", { name: "Single-point calculation" })
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
            await screen.findByRole("heading", { name: "Frequency calculation" })
            expect(screen.queryByTestId("energy-display-value")).not.toBeInTheDocument()
        })
    })

    it("demotes the dependency graph and review history below the evidence sections (results, geometries, on-demand)", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })
        const headingOrder = screen.getAllByRole("heading", { level: 2 }).map((el) => el.textContent)
        const dependenciesIndex = headingOrder.indexOf("Dependency graph")
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
        // `invalid_handle` is what live traffic actually returns for a
        // malformed-but-right-prefix ref, distinct from the
        // `handle_type_mismatch` case above. Pins the shared
        // `INVALID_HANDLE_CODES` classification in `useScientificRecord` on
        // this page too.
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
        // Structurally inapplicable sections (per the calculation's TYPE)
        // must not register a table-of-contents entry at all -- not even
        // a "not applicable" one -- while a section that IS applicable to
        // the type but merely has no data for this record keeps its
        // entry and its explanation. See `LazySection`'s own docstring
        // for the `applicable` vs `available` distinction this covers.

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
            await screen.findByRole("heading", { name: "Single-point calculation" })

            const toc = await screen.findByRole("navigation", { name: "Sections on this page" })
            await waitFor(() => expect(within(toc).getAllByRole("link").length).toBeGreaterThan(5))
            const labels = within(toc).getAllByRole("link").map((link) => link.textContent)

            for (const inapplicable of [
                "Geometry validation", "Constraints", "Vibrational modes",
                "Scan trajectory", "IRC trajectory", "Path-search trajectory",
            ]) {
                expect(labels).not.toContain(inapplicable)
            }
            // Sections an sp calculation CAN have are unaffected.
            for (const applicable of ["Result", "SCF stability", "Artifacts", "Imaginary-mode projections"]) {
                expect(labels).toContain(applicable)
            }
        })

        it("includes IRC trajectory (and excludes scan/path-search/vibrational-mode/geometry-validation) for an irc calculation", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json({
                record: mockRecord({
                    calculation: { ...mockRecord().calculation, type: "irc" },
                    results: {
                        kind: "irc",
                        irc: { direction: "forward", has_forward: true, has_reverse: false },
                        sp: null, opt: null, freq: null, scan: null, path_search: null,
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
                        has_constraints: true, constraints_applicable: true,
                        has_freq_modes: false, freq_modes_applicable: false,
                        has_scan: false, scan_applicable: false,
                        has_irc: true, irc_applicable: true,
                        has_path_search: false, path_search_applicable: false,
                    },
                }),
            })))
            page()
            await screen.findByRole("heading", { name: "IRC calculation" })

            const toc = await screen.findByRole("navigation", { name: "Sections on this page" })
            await waitFor(() => expect(within(toc).getAllByRole("link").length).toBeGreaterThan(5))
            const labels = within(toc).getAllByRole("link").map((link) => link.textContent)

            expect(labels).toContain("IRC trajectory")
            // `irc` structurally allows constraints (a constrained IRC
            // setup) -- unlike the sp fixture above, this entry stays.
            expect(labels).toContain("Constraints")
            for (const inapplicable of ["Geometry validation", "Vibrational modes", "Scan trajectory", "Path-search trajectory"]) {
                expect(labels).not.toContain(inapplicable)
            }
        })

        it("still renders imaginary-mode projections with its real content when a Hessian IS stored, so the omission rule cannot swallow the data-dependent case", async () => {
            // Base fixture: `freq` type, `has_hessian: true`, a populated
            // `imaginary_mode_projections` payload. Applicability here is
            // keyed on `has_hessian`, not on calculation type (see
            // `ImaginaryModeProjectionsSection`), so this section is
            // exactly the "applicable to the type, data-dependent" case
            // the brief distinguishes from structural inapplicability --
            // asserted positively, not just "it did not disappear".
            server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
            page()
            await screen.findByRole("heading", { name: "Frequency calculation" })

            fireEvent.click(screen.getByRole("heading", { name: "Imaginary-mode projections" }))
            const section = screen.getByRole("heading", { name: "Imaginary-mode projections" }).closest("details") as HTMLElement
            await within(section).findByText("Imaginary-mode projections loaded.")
            expect(ddFor(section, "Status")).toBe("determined")
        })
    })
})
