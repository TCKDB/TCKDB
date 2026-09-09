import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import LevelOfTheoryPage from "./LevelOfTheoryPage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

function page(ref = "lot_b3lyp") {
    return render(
        <MemoryRouter initialEntries={[`/methods/${ref}`]}>
            <Routes>
                <Route path="/methods/:lotRef" element={<LevelOfTheoryPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

const ATOM_ELEMENTS = ["H", "C", "N", "O", "F", "S", "Cl", "Br"]
const BOND_KEYS = [
    "C-H", "C-C", "C=C", "C#C", "C-N", "C=N", "C#N", "C-O", "C=O", "C-F", "C-S", "C=S", "C-Cl", "C-Br",
    "H-H", "H-N", "H-O", "N-N", "N=N", "N#N", "N-O", "N=O", "O-O", "O=O", "O-S", "O=S",
    "S-S", "S=S", "F-F", "Cl-Cl", "Br-Br", "F-Cl", "F-Br", "Cl-Br",
    "C-Cl2", "C-Br2", "N-S", "N-F", "N-Cl", "N-Br", "O-F", "O-Cl", "O-Br", "S-F", "S-Cl", "S-Br",
].slice(0, 45)

function atomCorrections() {
    return ATOM_ELEMENTS.map((el, i) => ({ correction_kind: "atom", target: el, value: -1.0 * (i + 1), component_kind: null }))
}

function bondCorrections() {
    return BOND_KEYS.map((key, i) => ({ correction_kind: "bond", target: key, value: 0.1 * (i + 1), component_kind: null }))
}

function baseRecord(overrides: Record<string, unknown> = {}) {
    return {
        level_of_theory: {
            level_of_theory_ref: "lot_b3lyp",
            method: "b3lyp",
            basis: "def2tzvp",
            aux_basis: null,
            cabs_basis: null,
            dispersion: null,
            solvent: null,
            solvent_model: null,
            keywords: null,
            spin_treatment: null,
            lot_hash: "hash-b3lyp",
            created_at: "2026-07-21T11:59:29Z",
        },
        evidence_summary: { calculation_usage_count: 416, has_correction_schemes: true, has_frequency_scale_factors: true, distinct_software_count: 1 },
        available_sections: { has_correction_schemes: true, has_frequency_scale_factors: true, has_used_by: true, has_software: true },
        correction_schemes: [
            {
                energy_correction_scheme: {
                    energy_correction_scheme_ref: "ecs_atom",
                    name: "atom_energy",
                    scheme_kind: "atom_energy",
                    version: null,
                    units: "hartree",
                    note: "Per-species AEC computed by Arkane.",
                    created_at: "2026-07-21T11:59:29Z",
                },
                level_of_theory: null,
                // Measured live shape (#439, deployed): empty
                // `software_release_ref` (the row stores a vendor, not a
                // release, to point at), `workflow_tool_release` null.
                software_release: { software_release_ref: "", software: "Gaussian", version: null },
                workflow_tool_release: null,
                literature: null,
                evidence_summary: { atom_param_count: 8, bond_param_count: 0, component_param_count: 0, has_corrections: true, applied_usage_count: 82, has_applied_usage: true, has_literature_source: false },
                available_sections: { has_corrections: true, has_used_by: true, has_literature: false },
                corrections: atomCorrections(),
            },
            {
                energy_correction_scheme: {
                    energy_correction_scheme_ref: "ecs_bond",
                    name: "bac_petersson",
                    scheme_kind: "bac_petersson",
                    version: null,
                    units: "kcal_mol",
                    note: "Per-species BAC computed by Arkane (bac_type=p).",
                    created_at: "2026-07-21T11:59:29Z",
                },
                level_of_theory: null,
                software_release: { software_release_ref: "", software: "Gaussian", version: null },
                workflow_tool_release: null,
                literature: null,
                evidence_summary: { atom_param_count: 0, bond_param_count: 45, component_param_count: 0, has_corrections: true, applied_usage_count: 82, has_applied_usage: true, has_literature_source: false },
                available_sections: { has_corrections: true, has_used_by: true, has_literature: false },
                corrections: bondCorrections(),
            },
        ],
        frequency_scale_factors: [
            {
                scale_kind: "fundamental",
                value: 0.999,
                frequency_scale_factor_count: 10,
                frequency_scale_factors: Array.from({ length: 10 }, (_, i) => ({
                    frequency_scale_factor_ref: `fsf_${i}`,
                    software_release: { software_release_ref: "", software: "Gaussian", version: null },
                    workflow_tool_release: { workflow_tool_release_ref: `wfr_${i}`, workflow_tool: "ARC", version: "1.1.0" },
                    source_literature_ref: null,
                })),
            },
        ],
        used_by: [{ calculation_ref: "calc_one", calculation_id: null, endpoint: "/api/v1/scientific/calculations/calc_one", type: "opt", record_type: "species_entry", record_ref: "spe_one", record_endpoint: "/api/v1/scientific/species-entries/spe_one" }],
        software: { software: [{ software: "Gaussian", version: "16", calculation_count: 416 }], workflow_tools: [{ workflow_tool: "ARC", version: "1.1.0", calculation_count: 416 }] },
        ...overrides,
    }
}

function mockResponse(record: Record<string, unknown>) {
    return { request: { include: [] }, review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 }, record }
}

describe("LevelOfTheoryPage: the real per-LOT record page", () => {
    it("renders identity, software, and full correction-scheme parameter tables -- not summarised or truncated", async () => {
        server.use(http.get("/api/v1/scientific/level-of-theories/lot_b3lyp", () => HttpResponse.json(mockResponse(baseRecord()))))
        const user = userEvent.setup()
        page()
        expect(await screen.findByRole("heading", { name: "b3lyp/def2tzvp", level: 1 })).toBeVisible()
        const softwareTable = screen.getByRole("table", { name: "Software and workflow tools observed at this level of theory" })
        expect(within(softwareTable).getByText("Gaussian")).toBeVisible()

        // Each scheme is now its own collapsible box (owner: "expandable
        // boxes for AEC and BAC") -- opened here via its own summary,
        // exactly as a reader would, before asserting the full table
        // underneath is intact and not summarised.
        await user.click(screen.getByText("Atom-energy correction").closest("summary") as HTMLElement)
        await user.click(screen.getByText("Petersson bond-additivity correction").closest("summary") as HTMLElement)

        const atomTable = screen.getByRole("table", { name: "Element correction parameters" })
        expect(within(atomTable).getAllByRole("row")).toHaveLength(1 + 8) // header + 8 elements, per the task brief's own acceptance criterion
        expect(within(atomTable).getByText("Br")).toBeVisible()

        const bondTable = screen.getByRole("table", { name: "Bond correction parameters" })
        expect(within(bondTable).getAllByRole("row")).toHaveLength(1 + 45) // header + 45 bonds
    })

    /**
     * Owner ruling, verbatim: "should be more like also expandable boxes
     * for AEC and BAC but their names are the software or something."
     * Each scheme is its own collapsible `<details>` box titled by its
     * kind PLUS its software -- never the depositor's own free-text
     * `energy_correction_scheme.name` (constructed here as something a
     * controlled-vocabulary label could never produce, same technique
     * `CorrectionSchemePage.test.tsx`'s own mutation-table test uses, so a
     * regression that reverts to `name` is unambiguous).
     *
     * MUTATION TABLE (a): title a box from the depositor `name` --
     * `git stash` the fix, this test fails because "Bob's atom energy
     * hack" appears on the page (or the software-titled query below finds
     * nothing).
     */
    it("titles each correction-scheme box by its kind and its software, never the depositor's free-text name", async () => {
        server.use(http.get("/api/v1/scientific/level-of-theories/lot_b3lyp", () => HttpResponse.json(mockResponse(baseRecord({
            correction_schemes: [
                { ...baseRecord().correction_schemes[0], energy_correction_scheme: { ...baseRecord().correction_schemes[0].energy_correction_scheme, name: "Bob's atom energy hack" } },
                baseRecord().correction_schemes[1],
            ],
        })))))
        page()
        await screen.findByRole("heading", { name: "b3lyp/def2tzvp", level: 1 })
        expect(screen.getByText("Atom-energy correction")).toBeVisible()
        expect(screen.getAllByText("Gaussian").length).toBeGreaterThan(0)
        expect(screen.queryByText("Bob's atom energy hack")).not.toBeInTheDocument()
    })

    /**
     * MUTATION TABLE (b): drop the roll-up from the collapsed summary.
     * Each box's `<summary>` carries a true parameter-count roll-up
     * (`schemeParameterRollup`, computed from the same `evidence_summary`
     * counts the open table itself renders from) alongside its title, so
     * a reader who never opens a box still learns how much is in it --
     * the exact defect `EvidenceChecklist.tsx`'s own docstring documents
     * fixing elsewhere on this app (a collapsed card answering "6 rows"
     * when the real figure was 4). Both boxes are collapsed by default
     * (never `open`), and the roll-up is present regardless.
     */
    it("carries a true parameter-count roll-up in each box's collapsed summary", async () => {
        server.use(http.get("/api/v1/scientific/level-of-theories/lot_b3lyp", () => HttpResponse.json(mockResponse(baseRecord()))))
        page()
        await screen.findByRole("heading", { name: "b3lyp/def2tzvp", level: 1 })
        const atomBox = screen.getByText("Atom-energy correction").closest("details") as HTMLDetailsElement
        const bondBox = screen.getByText("Petersson bond-additivity correction").closest("details") as HTMLDetailsElement
        expect(atomBox).not.toHaveAttribute("open")
        expect(bondBox).not.toHaveAttribute("open")
        expect(within(atomBox).getByText("8 parameters")).toBeVisible()
        expect(within(bondBox).getByText("45 parameters")).toBeVisible()
    })

    /**
     * A level of theory whose software could not resolve to one vendor
     * (the backfill leaves `software_id` null in exactly this case) still
     * gets a titled, openable box -- falling back to text that reads as
     * an archive-side absence, styled through the muted pill (same
     * present/absent tone split `EvidenceChecklist.tsx` uses), never
     * blank and never the depositor's `name`.
     */
    it("falls back to a muted 'software not recorded' badge, never blank or the depositor name, when software is absent", async () => {
        server.use(http.get("/api/v1/scientific/level-of-theories/lot_b3lyp", () => HttpResponse.json(mockResponse(baseRecord({
            correction_schemes: [
                { ...baseRecord().correction_schemes[0], software_release: null },
                baseRecord().correction_schemes[1],
            ],
        })))))
        page()
        await screen.findByRole("heading", { name: "b3lyp/def2tzvp", level: 1 })
        const badge = screen.getByText("software not recorded")
        expect(badge).toBeVisible()
        expect(badge).toHaveClass("value-pill--muted")
    })

    /**
     * MUTATION TABLE (b): absence wording. `docs/plans/methods-surface.md`
     * §4.2 item 3 / §2.6's house-rule caution: "no scheme deposited" is a
     * fact about the archive, never rendered as "not applicable" (a claim
     * about chemistry this archive cannot make). Constructed fixture: a
     * LOT with `correction_schemes: []` and `has_correction_schemes:
     * false` -- the wb97xd/CCSD(T)-F12 shape measured live.
     */
    it("states a missing correction scheme as a plain archive-side absence, never 'not applicable'", async () => {
        server.use(http.get("/api/v1/scientific/level-of-theories/lot_b3lyp", () => HttpResponse.json(mockResponse(baseRecord({
            evidence_summary: { calculation_usage_count: 78, has_correction_schemes: false, has_frequency_scale_factors: true, distinct_software_count: 1 },
            available_sections: { has_correction_schemes: false, has_frequency_scale_factors: true, has_used_by: true, has_software: true },
            correction_schemes: [],
        })))))
        page()
        await screen.findByRole("heading", { name: "b3lyp/def2tzvp", level: 1 })
        expect(screen.getByText("No atom-energy correction scheme is deposited for this level of theory.")).toBeVisible()
        expect(screen.getByText("No petersson bond-additivity correction scheme is deposited for this level of theory.")).toBeVisible()
        expect(screen.queryByText(/not applicable/i)).not.toBeInTheDocument()
    })

    it("states a missing frequency scale factor as a plain archive-side absence", async () => {
        server.use(http.get("/api/v1/scientific/level-of-theories/lot_b3lyp", () => HttpResponse.json(mockResponse(baseRecord({
            evidence_summary: { calculation_usage_count: 39, has_correction_schemes: false, has_frequency_scale_factors: false, distinct_software_count: 1 },
            available_sections: { has_correction_schemes: false, has_frequency_scale_factors: false, has_used_by: true, has_software: true },
            correction_schemes: [],
            frequency_scale_factors: [],
        })))))
        page()
        await screen.findByRole("heading", { name: "b3lyp/def2tzvp", level: 1 })
        expect(screen.getByText("No frequency scale factor is deposited for this level of theory.")).toBeVisible()
        expect(screen.queryByText(/not applicable/i)).not.toBeInTheDocument()
    })

    /**
     * MUTATION TABLE (c): frequency-scale-factor dedup. §4.2 item 4: 10
     * distinct `frequency_scale_factor_ref`s that all evaluate to the same
     * `(fundamental, 0.999)` render as ONE visible value with a disclosure
     * listing the 10 refs -- never 10 visible value rows. This test counts
     * the rendered value nodes directly (the exact mutation the task
     * brief's own mutation table names: "remove the dedup step, watch a
     * test counting rendered FSF value nodes fail").
     */
    it("renders 10 near-duplicate frequency-scale-factor rows as ONE visible value with a provenance disclosure", async () => {
        server.use(http.get("/api/v1/scientific/level-of-theories/lot_b3lyp", () => HttpResponse.json(mockResponse(baseRecord()))))
        page()
        await screen.findByRole("heading", { name: "b3lyp/def2tzvp", level: 1 })
        // Exactly one rendered "0.999" value node -- the mutation this
        // guards against is a build that renders 10 separate value rows
        // (one per underlying `frequency_scale_factor_ref`) instead of one
        // value with a provenance disclosure underneath it.
        expect(screen.getAllByText("0.999")).toHaveLength(1)
        const disclosure = screen.getByText("Provenance").closest("details") as HTMLDetailsElement
        expect(disclosure).not.toBeNull()
        expect(disclosure).not.toHaveAttribute("open") // collapsed by default, not shown open on first paint
        expect(within(disclosure).getByText("Provenance").closest("summary")).toHaveTextContent("Provenance (10)")
        // The 10 distinct underlying refs are still all present in the
        // document (inside the disclosure body) -- never collapsed away,
        // only demoted behind the closed `<details>`.
        const refLinks = within(disclosure).getAllByRole("link", { name: /^fsf_/ })
        expect(refLinks).toHaveLength(10)
        expect(refLinks[0]).toHaveAttribute("href", "/methods/frequency-scale-factors/fsf_0")
    })

    it("links each correction scheme's own ref into its standalone /methods/schemes/:ecsRef page", async () => {
        server.use(http.get("/api/v1/scientific/level-of-theories/lot_b3lyp", () => HttpResponse.json(mockResponse(baseRecord()))))
        page()
        await screen.findByRole("heading", { name: "b3lyp/def2tzvp", level: 1 })
        const links = screen.getAllByRole("link", { name: "ecs_atom" })
        expect(links.length).toBeGreaterThan(0)
        expect(links[0]).toHaveAttribute("href", "/methods/schemes/ecs_atom")
    })
})
