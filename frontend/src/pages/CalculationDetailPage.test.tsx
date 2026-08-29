import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
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

    it("reports a check as recorded/not recorded, never as a pass/fail verdict, on the summary card", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: mockRecord() })))
        page()
        await screen.findByRole("heading", { name: "Frequency calculation" })

        const summary = screen.getByLabelText("Calculation evidence summary")
        const coverage = summary.querySelector(".coverage-card") as HTMLElement
        // Scoped to the <strong> line itself — the coverage card's caveat
        // sentence legitimately contains the word "passed" ("not that it
        // passed"), so asserting over the whole card would pass whether or
        // not the summary line itself ever named a pass/fail verdict.
        const verdictLine = coverage.querySelector("strong") as HTMLElement
        expect(within(verdictLine).getByText(/geometry validation\s*not recorded/)).toBeVisible()
        expect(within(verdictLine).queryByText(/passed/i)).not.toBeInTheDocument()
        expect(within(verdictLine).queryByText(/fail/i)).not.toBeInTheDocument()
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
})
