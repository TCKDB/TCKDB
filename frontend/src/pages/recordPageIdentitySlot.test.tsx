import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import CalculationDetailPage from "./CalculationDetailPage"
import ConformerGroupPage from "./ConformerGroupPage"
import ConformerObservationPage from "./ConformerObservationPage"
import GeometryDetailPage from "./GeometryDetailPage"
import SpeciesEntryPage from "./SpeciesEntryPage"

/**
 * ToC-top-alignment (follow-up to #321/#322/#323/#325): five record pages
 * (species entry, calculation, geometry, conformer basin, conformer
 * observation) each compose a header a different way -- some already
 * nested it inside `<PageShell>`'s children, one (`SpeciesEntryPage`)
 * rendered it entirely outside `<PageShell>`, spanning full width above
 * the ToC/content flex row. All five now route their header through
 * `PageShell`'s `identity` prop instead, so the reserved ToC column
 * begins level with the header on every one of them -- not only on
 * whichever page a reader happened to check.
 *
 * This is the table-driven test the design brief calls for by name: "the
 * assertion that catches 'worked on the one page I checked'". Each entry
 * below mounts its real page against a minimal-but-schema-valid fixture
 * (borrowed from that page's own test file) and asserts the SAME
 * structural relationship -- `.page-shell-identity` is the first child of
 * `.page-shell-content`, and it actually contains this page's own
 * heading -- rather than five different one-off checks that could drift
 * apart from each other.
 */

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function assertIdentityLeadsContentColumn(container: HTMLElement, headingName: string) {
    const layout = container.querySelector(".page-shell-layout")
    const content = container.querySelector(".page-shell-content")
    const identitySlot = container.querySelector(".page-shell-identity")
    expect(layout).not.toBeNull()
    expect(content).not.toBeNull()
    expect(identitySlot).not.toBeNull()
    // Positive containment, not just "exists somewhere on the page".
    expect((content as HTMLElement).contains(identitySlot as Node)).toBe(true)
    // First child of the content column -- ahead of the rest of the page's
    // own content, so the ToC lines up against the header, not the middle
    // of the page.
    expect((content as HTMLElement).firstElementChild).toBe(identitySlot)
    // And the slot really holds THIS page's own header, not an
    // incidental first child that happens to sit in the right place.
    const heading = screen.getByRole("heading", { name: headingName })
    expect((identitySlot as HTMLElement).contains(heading)).toBe(true)
}

describe("Every record page routes its header through PageShell's identity slot", () => {
    it("GeometryDetailPage: the geometry header leads the content column", async () => {
        server.use(http.get("/api/v1/scientific/geometries/geom_ch4_one", () => HttpResponse.json({
            geometry_ref: "geom_ch4_one",
            natoms: 5,
            geom_hash: "ab12cd34".repeat(8),
            format: "cartesian",
            coordinate_units: "angstrom",
            atoms: [
                { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
                { atom_index: 2, element: "H", x: 0.11, y: 0.22, z: 0.33 },
                { atom_index: 3, element: "H", x: -0.63, y: -0.63, z: 0.63 },
                { atom_index: 4, element: "H", x: -0.63, y: 0.63, z: -0.63 },
                { atom_index: 5, element: "H", x: 0.63, y: -0.63, z: -0.63 },
            ],
            xyz_text: "5\n\nC 0.000000 0.000000 0.000000\nH 0.110000 0.220000 0.330000\nH -0.630000 -0.630000 0.630000\nH -0.630000 0.630000 -0.630000\nH 0.630000 -0.630000 -0.630000",
            created_at: "2026-07-21T12:06:50.748258",
            identity: null,
            provenance: { produced_by: [], used_as_input_by: [] },
        })))
        const { container } = render(
            <MemoryRouter initialEntries={["/geometries/geom_ch4_one"]}>
                <Routes>
                    <Route path="/geometries/:geometryRef" element={<GeometryDetailPage />} />
                </Routes>
            </MemoryRouter>,
        )
        await screen.findByRole("heading", { name: "CH4 geometry" })
        assertIdentityLeadsContentColumn(container, "CH4 geometry")
    })

    it("CalculationDetailPage: the calculation header leads the content column", async () => {
        server.use(http.get("/api/v1/scientific/calculations/calc_freq_one", () => HttpResponse.json({
            record: {
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
                        species_ref: "spc_demo", species_entry_ref: "spe_demo", species_entry_label: "ground state",
                        canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
                        charge: 0, multiplicity: 2, species_entry_kind: "minimum", electronic_state_kind: "ground",
                    },
                    transition_state_entry: null,
                },
                level_of_theory: null,
                software_release: null,
                workflow_tool_release: null,
                literature: null,
                provenance: {
                    has_result: false, converged: null,
                    geometry_validation_status: "not_present", scf_stability_status: "not_present",
                },
                available_sections: {
                    has_results: false, has_dependencies: false, has_parameters: false, has_constraints: false,
                    has_artifacts: false, has_input_geometries: false, has_output_geometries: false,
                    has_geometry_validation: false, has_scf_stability: false, has_wavefunction_diagnostic: false,
                    has_spin_diagnostic: false, has_freq_modes: false, has_hessian: false, has_scan: false,
                    has_irc: false, has_path_search: false, has_execution_environment: false,
                    has_energy_corrections: false,
                },
                results: { kind: "freq", sp: null, opt: null, scan: null, irc: null, path_search: null, freq: null },
                dependencies: [],
                input_geometries: [],
                output_geometries: [],
                review_history: [],
                scan: null, irc: null, path_search: null, execution_environment: null,
            },
        })))
        const { container } = render(
            <MemoryRouter initialEntries={["/calculations/calc_freq_one"]}>
                <Routes>
                    <Route path="/calculations/:calculationRef" element={<CalculationDetailPage />} />
                </Routes>
            </MemoryRouter>,
        )
        await screen.findByRole("heading", { name: "Frequency of [CH3]" })
        assertIdentityLeadsContentColumn(container, "Frequency of [CH3]")
    })

    it("ConformerObservationPage: the observation header leads the content column", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => HttpResponse.json({
            record: {
                conformer_observation: {
                    conformer_observation_ref: "co_one", scientific_origin: "computed", note: null,
                    created_at: "2026-07-21T12:06:50.748258", review: { status: "reviewed" },
                },
                conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", note: null, review: { status: "not_reviewed" } },
                species: { species_ref: "spc_demo", species_entry_ref: "spe_demo", species_entry_label: "ground state", canonical_smiles: "[CH3]" },
                assignment_scheme: null,
                evidence_summary: {
                    calculation_count: 0, geometry_count: 0, has_opt: false, has_freq: false, has_sp: false,
                    has_geometry_validation: false, has_scf_stability: false, levels_of_theory: {},
                },
                available_sections: {
                    has_observations: true, has_selections: false, has_calculations: true, has_geometries: true, has_review: true,
                },
                observations: [],
                calculations: [],
                geometries: [],
                review_history: [],
                selections: [],
            },
        })))
        const { container } = render(
            <MemoryRouter initialEntries={["/conformer-observations/co_one"]}>
                <Routes>
                    <Route path="/conformer-observations/:observationRef" element={<ConformerObservationPage />} />
                </Routes>
            </MemoryRouter>,
        )
        await screen.findByRole("heading", { name: "Computed observation" })
        assertIdentityLeadsContentColumn(container, "Computed observation")
    })

    it("ConformerGroupPage: the basin header leads the content column", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json({
            record: {
                conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", note: null, review: { status: "not_reviewed" } },
                species: { species_ref: "spc_demo", species_entry_ref: "spe_demo", species_entry_label: "ground state", canonical_smiles: "[CH3]" },
                observations_summary: { total: 0, by_scientific_origin: {} },
                evidence_summary: {
                    calculation_count: 0, optimization_chain_count: 0, geometry_count: 0,
                    evidence_coverage: { opt: 0, freq: 0, sp: 0 },
                },
                observations: [],
                calculations: [],
                geometries: [],
            },
        })))
        const { container } = render(
            <MemoryRouter initialEntries={["/conformer-groups/cg_demo"]}>
                <Routes>
                    <Route path="/conformer-groups/:groupRef" element={<ConformerGroupPage />} />
                </Routes>
            </MemoryRouter>,
        )
        // The h1 now states what the record IS ("Conformer basin"), not
        // the producer's own deposited label -- see
        // `ConformerGroupPage.tsx`'s header comment. The fixture keeps
        // `label: "conformer_1"` on purpose; this file only checks that
        // whatever the header renders leads the content column.
        await screen.findByRole("heading", { name: "Conformer basin" })
        assertIdentityLeadsContentColumn(container, "Conformer basin")
    })

    it("SpeciesEntryPage: EntryIdentity leads the content column (previously rendered entirely outside PageShell)", async () => {
        const entryRef = "spe_demo"
        server.use(
            http.get("/api/v1/scientific/species/search", () => HttpResponse.json({
                records: [{
                    species_ref: "spc_demo",
                    canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3",
                    charge: 0, multiplicity: 2,
                    entries: [{
                        species_entry_ref: entryRef, species_entry_kind: "minimum", electronic_state_kind: "ground",
                        review: { status: "not_reviewed" },
                        availability: { has_thermo: false, has_statmech: false, has_transport: false, has_conformers: false, calculation_count: 0 },
                    }],
                }],
            })),
            http.get("/api/v1/scientific/conformers/search", () => HttpResponse.json({ records: [] })),
            http.get("/api/v1/scientific/species-calculations/search", () => HttpResponse.json({ records: [] })),
        )
        const { container } = render(
            <MemoryRouter initialEntries={[`/species-entries/${entryRef}`]}>
                <Routes>
                    <Route path="/species-entries/:entryRef" element={<SpeciesEntryPage />} />
                    <Route path="/species-entries/:entryRef/:section" element={<SpeciesEntryPage />} />
                </Routes>
            </MemoryRouter>,
        )
        await screen.findByRole("heading", { name: "CH3" })
        assertIdentityLeadsContentColumn(container, "CH3")
    })
})
