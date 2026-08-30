import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"

// ---------------------------------------------------------------------------
// Two DISTINCT conformer groups, each with its own observations and its own
// non-overlapping calculation/geometry refs -- required by the design brief:
// a fixture with only one conformer can prove that the selector renders
// SOMETHING, never that it renders the SELECTED conformer's own evidence.
// Group "conformer_1" additionally carries two observations (one with no SP
// calculation, one with TWO), so the "1 / 4 / many" conformer-count story,
// the "multiple deposits stay multiple" rule, and a real multi-SP-per-
// observation case all have fixture coverage.
//
// Thermo and statmech each carry THREE records: one linked to the SELECTED
// conformer, one linked to the OTHER named conformer, and one with no
// conformer link at all -- the exact three-way shape
// `partitionByConformerLink` (`domain/conformerEvidence.ts`) exists to
// express, and the exact shape a binary matched/unmatched split gets wrong
// in the middle case (mislabeling "linked to a different conformer" as "no
// link").
// ---------------------------------------------------------------------------

const entryRef = "spe_bcbdjwkip75yoziblpntwzblzu"
const groupOneRef = "cg_one"
const groupTwoRef = "cg_two"
const lot = { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }
const server = setupServer()

type HandlerOptions = {
    empty?: boolean
    malformed?: boolean
    status?: number
    noConformers?: boolean
    singleConformer?: boolean
    statmechConformersIncludeFails?: boolean
}

function conformerRecords() {
    return [
        {
            conformer_group: { conformer_group_ref: groupOneRef, label: "conformer_1" },
            observations_summary: { total: 2 },
            evidence_summary: {
                calculation_count: 7,
                optimization_chain_count: 2,
                geometry_count: 2,
                // Real semantics: the number of OBSERVATIONS having at least
                // one calculation of that type, never more than
                // observations_summary.total (2 here). Both co_1 and co_2
                // have an opt calc (opt: 2); both have a freq calc (freq:
                // 2); only co_1 has an sp calc (sp: 1). A prior fixture bug
                // used the raw calculation count instead (opt: 3, which is
                // impossible against total: 2) and enshrined it in an
                // assertion -- fixed here, the one place someone would look
                // this semantics up.
                evidence_coverage: { opt: 2, freq: 2, sp: 1 },
                levels_of_theory: { opt: [lot], freq: [lot], sp: [lot] },
            },
            observations: [
                {
                    // TWO sp calculations on one observation -- proves the
                    // SP tab renders every one of an observation's sp
                    // calculations, not just the first.
                    conformer_observation: { conformer_observation_ref: "co_1" },
                    calculations: [
                        { calculation_ref: "calc_opt_1a", type: "opt", level_of_theory: lot },
                        { calculation_ref: "calc_opt_1b", type: "opt", level_of_theory: lot },
                        { calculation_ref: "calc_freq_1", type: "freq", level_of_theory: lot },
                        {
                            calculation_ref: "calc_sp_1", type: "sp", level_of_theory: lot,
                            software_release: { software_release_ref: "srel_1", software: "Gaussian", version: "Gaussian 16" },
                            workflow_tool_release: { workflow_tool_release_ref: "wfr_1", workflow_tool: "ARC", version: "1.1.0" },
                        },
                        {
                            calculation_ref: "calc_sp_1b", type: "sp", level_of_theory: lot,
                            software_release: { software_release_ref: "srel_1", software: "Gaussian", version: "Gaussian 16" },
                        },
                    ],
                },
                {
                    // No SP calculation on this observation -- mirrors CH3's
                    // live fourth observation (sp coverage 1/2, not 2/2).
                    conformer_observation: { conformer_observation_ref: "co_2" },
                    calculations: [
                        { calculation_ref: "calc_opt_2", type: "opt", level_of_theory: lot },
                        { calculation_ref: "calc_freq_2", type: "freq", level_of_theory: lot },
                    ],
                },
            ],
            calculations: [
                { calculation_ref: "calc_opt_1a", type: "opt" }, { calculation_ref: "calc_opt_1b", type: "opt" },
                { calculation_ref: "calc_freq_1", type: "freq" }, { calculation_ref: "calc_sp_1", type: "sp" },
                { calculation_ref: "calc_sp_1b", type: "sp" },
                { calculation_ref: "calc_opt_2", type: "opt" }, { calculation_ref: "calc_freq_2", type: "freq" },
            ],
            geometries: [
                { calculation_ref: "calc_opt_1a", geometry: { geometry_ref: "geom_g1", geom_hash: "hashg1000000", natoms: 4, role: "final" } },
                { calculation_ref: "calc_opt_1b", geometry: { geometry_ref: "geom_g1", geom_hash: "hashg1000000", natoms: 4, role: "final" } },
                { calculation_ref: "calc_opt_2", geometry: { geometry_ref: "geom_g2", geom_hash: "hashg2000000", natoms: 4, role: "final" } },
            ],
        },
        {
            conformer_group: { conformer_group_ref: groupTwoRef, label: "conformer_2" },
            observations_summary: { total: 1 },
            evidence_summary: {
                calculation_count: 3,
                optimization_chain_count: 1,
                geometry_count: 1,
                evidence_coverage: { opt: 1, freq: 1, sp: 1 },
                levels_of_theory: { opt: [lot], freq: [lot], sp: [lot] },
            },
            observations: [{
                conformer_observation: { conformer_observation_ref: "co_3" },
                calculations: [
                    { calculation_ref: "calc_opt_3", type: "opt", level_of_theory: lot },
                    { calculation_ref: "calc_freq_3", type: "freq", level_of_theory: lot },
                    { calculation_ref: "calc_sp_3", type: "sp", level_of_theory: lot },
                ],
            }],
            calculations: [
                { calculation_ref: "calc_opt_3", type: "opt" }, { calculation_ref: "calc_freq_3", type: "freq" },
                { calculation_ref: "calc_sp_3", type: "sp" },
            ],
            geometries: [
                { calculation_ref: "calc_opt_3", geometry: { geometry_ref: "geom_g3", geom_hash: "hashg3000000", natoms: 4, role: "final" } },
            ],
        },
    ]
}

function thermoRecords() {
    return [
        {
            // Linked to the conformer that's selected by default (cg_one).
            thermo_ref: "thm_one",
            scientific_origin: "computed", model_kind: "nasa",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
            supersession: null,
            h298_kj_mol: 143.9, s298_j_mol_k: 194.4, h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
            nasa: null, nasa9: null, wilhoit: null, points: null, temperature_coverage: null,
            evidence_completeness: {
                score: 6, max: 8,
                checklist: {
                    has_source_calculations: true, has_statmech_source: true, has_frequency_evidence: true,
                    has_sp_or_energy_evidence: true, has_temperature_dependent_model: true, has_uncertainty: false,
                    has_geometry_validation: true, has_scf_stability: false,
                },
            },
            provenance: {
                primary_calculation: {
                    calculation_ref: "calc_sp_1",
                    calculation_type: "sp",
                    converged: null,
                    geometry_validation_status: "not_present",
                    scf_stability_status: "not_present",
                    level_of_theory: lot,
                    software: { software_release_ref: "srel_1", software: "Gaussian", version: "Gaussian 16" },
                },
                level_of_theory: lot,
                software_release: null,
                workflow_tool_release: null,
                statmech_ref: "sm_1",
                freq_calculation_ref: "calc_freq_1",
                sp_calculation_ref: "calc_sp_1",
                conformer_observation_ref: "co_1",
                conformer_group_ref: groupOneRef,
            },
            group_additivity: null,
        },
        {
            // Population B: no resolvable primary calculation, Arkane
            // software, no conformer link -- the real archive shape
            // (thermo id 4, spe_dfcw4tvy6tkqxnyittmn6d3vdu), per PR #285.
            thermo_ref: "thm_two",
            scientific_origin: "computed", model_kind: "nasa",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
            supersession: null,
            h298_kj_mol: 192.4, s298_j_mol_k: 380.3, h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
            nasa: null, nasa9: null, wilhoit: null, points: null, temperature_coverage: null,
            evidence_completeness: { score: 3, max: 8, checklist: { has_source_calculations: false, has_statmech_source: true } },
            provenance: {
                primary_calculation: null,
                level_of_theory: null,
                software_release: { software_release_ref: "srel_arkane", software: "Arkane", version: "1.1.0" },
                workflow_tool_release: null,
                statmech_ref: null,
                freq_calculation_ref: null,
                sp_calculation_ref: null,
                conformer_observation_ref: null,
                conformer_group_ref: null,
            },
            group_additivity: null,
        },
        {
            // Linked to the OTHER named conformer (cg_two) -- the exact
            // shape a binary matched/unmatched split gets wrong: this
            // record IS linked to a basin, just not the one selected by
            // default. Must render under "From conformer_2", never lumped
            // into "no conformer link".
            thermo_ref: "thm_three",
            scientific_origin: "computed", model_kind: "nasa",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
            supersession: null,
            h298_kj_mol: 150.1, s298_j_mol_k: 200.2, h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
            nasa: null, nasa9: null, wilhoit: null, points: null, temperature_coverage: null,
            evidence_completeness: { score: 8, max: 8, checklist: {} },
            provenance: {
                primary_calculation: {
                    calculation_ref: "calc_sp_3", calculation_type: "sp", converged: null,
                    geometry_validation_status: "not_present", scf_stability_status: "not_present",
                    level_of_theory: lot, software: null,
                },
                level_of_theory: lot,
                software_release: null,
                workflow_tool_release: null,
                statmech_ref: "sm_3",
                freq_calculation_ref: "calc_freq_3",
                sp_calculation_ref: "calc_sp_3",
                conformer_observation_ref: "co_3",
                conformer_group_ref: groupTwoRef,
            },
            group_additivity: null,
        },
    ]
}

function statmechRecord(ref: string, withConformers: boolean, conformers: Array<{ conformer_group_ref: string; label: string }>) {
    return {
        statmech: {
            statmech_ref: ref, scientific_origin: "computed", statmech_treatment: "rrho",
            rigid_rotor_kind: "asymmetric_top", point_group: "D3h", external_symmetry: 6, is_linear: false,
            uses_projected_frequencies: null, optical_isomers: 1, rotational_constant_a_cm1: null,
            rotational_constant_b_cm1: null, rotational_constant_c_cm1: null,
            frequency_scale_factor_value: 0.999, note: null, created_at: "2026-07-29T08:26:29.315550",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        },
        supersession: null,
        species: {
            species_ref: "spc_atp56uqux2ajao7hvckx7gx7ca", species_entry_ref: entryRef,
            species_entry_label: null, canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
            charge: 0, multiplicity: 2,
        },
        transition_state: null,
        frequency_scale_factor: null,
        software_release: { software_release_ref: "srel_arkane", software: "Arkane", version: "1.1.0" },
        workflow_tool_release: null, literature: null,
        evidence_summary: {
            source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true,
            has_sp_calculation: true, sp_from_optimization: false, has_rotor_scans: false,
            torsion_count: 0, has_frequency_scale_factor: true, has_conformer_context: true,
        },
        available_sections: {
            has_source_calculations: true, has_torsions: false, has_electronic_levels: false,
            has_frequencies: true, has_conformers: true, has_review: true,
        },
        ...(withConformers ? { conformers } : {}),
    }
}

function statmechRecords(withConformers: boolean) {
    return [
        statmechRecord("sm_1", withConformers, [{ conformer_group_ref: groupOneRef, label: "conformer_1" }]),
        statmechRecord("sm_2", withConformers, []),
        statmechRecord("sm_3", withConformers, [{ conformer_group_ref: groupTwoRef, label: "conformer_2" }]),
    ]
}

function handlers(options: HandlerOptions = {}) {
    return [
        http.get("/api/v1/scientific/species/search", () => {
            if (options.status) return HttpResponse.json({ detail: "archive unavailable" }, { status: options.status })
            if (options.malformed) return HttpResponse.json({ records: [{ species_ref: "spc_bad" }] })
            if (options.empty) return HttpResponse.json({ records: [] })
            return HttpResponse.json({ records: [{
                species_ref: "spc_atp56uqux2ajao7hvckx7gx7ca",
                canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3",
                charge: 0, multiplicity: 2,
                entries: [{
                    species_entry_ref: entryRef, species_entry_kind: "minimum", electronic_state_kind: "ground",
                    review: { status: "not_reviewed" },
                    availability: { has_thermo: true, has_statmech: true, has_transport: false, has_conformers: true, calculation_count: 10 },
                }],
            }] })
        }),
        http.get("/api/v1/scientific/conformers/search", () => {
            if (options.status) return HttpResponse.json({ detail: "archive unavailable" }, { status: options.status })
            if (options.noConformers) return HttpResponse.json({ records: [] })
            if (options.singleConformer) return HttpResponse.json({ records: [conformerRecords()[0]] })
            // `conformers/search` orders by review rank, not by label number, so
            // the archive really can return conformer_2 ahead of conformer_1 --
            // measured on spe_mbdqifmaclaakukr7agxbuq3wa, which returns 3, 2, 1.
            if (options.reversedOrder) return HttpResponse.json({ records: [...conformerRecords()].reverse() })
            return HttpResponse.json({ records: conformerRecords() })
        }),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/thermo`, () => HttpResponse.json({
            species_entry_ref: entryRef,
            review_summary: { approved: 0, under_review: 0, not_reviewed: 3, deprecated: 0, rejected: 0, total: 3 },
            records: thermoRecords(),
            pagination: { offset: 0, limit: 50, returned: 3, total: 3, post_collapse_total: 3 },
        })),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/statmech`, ({ request }) => {
            const includesConformers = new URL(request.url).searchParams.getAll("include").includes("conformers")
            if (includesConformers && options.statmechConformersIncludeFails) {
                return HttpResponse.json({ detail: "conformer include unavailable" }, { status: 503 })
            }
            return HttpResponse.json({
                review_summary: { approved: 0, under_review: 0, not_reviewed: 3, deprecated: 0, rejected: 0, total: 3 },
                records: statmechRecords(includesConformers),
                pagination: { offset: 0, limit: 50, returned: 3, total: 3, post_collapse_total: 3 },
            })
        }),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/transport`, () => HttpResponse.json({
            review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 },
            records: [],
            pagination: { offset: 0, limit: 50, returned: 0, total: 0, post_collapse_total: 0 },
        })),
    ]
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

describe("species-entry page: identity and errors", () => {
    it("renders the CH3 identity without the removed availability card grid", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        const heading = await screen.findByRole("heading", { name: "CH3" })
        expect(heading).toBeVisible()
        // Proof this is the <Formula> component, not plain text -- both
        // satisfy the accessible-name query above (markup is flattened),
        // but only Formula emits a real <sub> for the subscript.
        const subscript = heading.querySelector("sub")
        expect(subscript).not.toBeNull()
        expect(subscript).toHaveTextContent("3")
        expect(screen.getByText("[CH3]")).toBeVisible()
        expect(screen.getByText("0 / doublet (2)")).toBeVisible()
        expect(screen.getByText("Conformers · thermo · statmech")).toBeVisible()

        // SMILES and InChIKey are ALWAYS visible chemistry identifiers in
        // the identity header -- never behind the References disclosure.
        // Labeled explicitly (and honestly: this record only carries an
        // InChIKey, never a full InChI, so the label says "InChIKey").
        const identifiers = screen.getByRole("list", { name: "Chemical identifiers" })
        expect(within(identifiers).getByText("SMILES")).toBeVisible()
        expect(within(identifiers).getByText("[CH3]")).toBeVisible()
        expect(within(identifiers).getByText("InChIKey")).toBeVisible()
        expect(within(identifiers).getByText("WCYWZMWISLQXQU-UHFFFAOYSA-N")).toBeVisible()
        expect(within(identifiers).queryByText("InChI", { exact: true })).not.toBeInTheDocument()

        // Public refs are collapsed by default (References disclosure) but
        // present, visible, and copyable once opened -- never hidden by
        // substituting a label for the ref itself. Only the two STABLE
        // refs (species, entry) live here now -- InChIKey moved to the
        // always-visible identifiers row above, not duplicated here.
        expect(screen.queryByText("spc_atp56uqux2ajao7hvckx7gx7ca")).not.toBeVisible()
        await user.click(screen.getByText("References (2)"))
        expect(screen.getByRole("link", { name: "spc_atp56uqux2ajao7hvckx7gx7ca" })).toHaveAttribute(
            "href", "/species/spc_atp56uqux2ajao7hvckx7gx7ca",
        )
        const refsPanel = screen.getByText("References (2)").closest("details") as HTMLElement
        expect(within(refsPanel).queryByText("InChIKey")).not.toBeInTheDocument()

        // The availability card grid ("Available in this entry" / "View
        // record section") is gone -- plain navigation replaced it.
        expect(screen.queryByText("Available in this entry")).not.toBeInTheDocument()
        expect(screen.queryByText("Unavailable in this entry")).not.toBeInTheDocument()
        expect(screen.queryByRole("link", { name: "View record section" })).not.toBeInTheDocument()
    })

    it("distinguishes empty, malformed-success, HTTP error, and loading states", async () => {
        server.use(...handlers({ empty: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        expect(screen.getByRole("heading", { name: "Loading species entry" })).toBeVisible()
        expect(await screen.findByRole("heading", { name: "Entry not found" })).toBeVisible()
        cleanup()
        server.use(...handlers({ malformed: true }))
        render(<App />)
        expect(await screen.findByRole("alert")).toHaveTextContent("Entry data could not be read")
        cleanup()
        server.use(...handlers({ status: 503 }))
        render(<App />)
        expect(await screen.findByRole("alert")).toHaveTextContent("Entry unavailable")
    })

    it("canonicalizes an unrecognized section path segment to the default tab's own path", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}/calculations`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        await waitFor(() => expect(window.location.pathname).toBe(`/species-entries/${entryRef}/geometry`))
        expect(screen.getByRole("tab", { name: "Geometry" })).toHaveAttribute("aria-selected", "true")
    })
})

describe("species-entry page: conformer picker", () => {
    it("defaults to the FIRST CARD AS DISPLAYED even when the archive returns a different order", async () => {
        // The wire hands back conformer_2 first (review rank), the cards render
        // conformer_1 first (label order). A default of `conformers[0]` would
        // highlight the second card -- which reads as a bug, not a ranking.
        server.use(...handlers({ reversedOrder: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        const cards = document.querySelectorAll(".conformer-card")
        expect(within(cards[0] as HTMLElement).getByRole("button", { name: /Conformer Group 1/ })).toHaveAttribute("aria-pressed", "true")
        expect(within(cards[1] as HTMLElement).getByRole("button", { name: /Conformer Group 2/ })).toHaveAttribute("aria-pressed", "false")
    })

    it("shows one basin card per conformer, each with its own distinct counts, and defaults to selecting the first", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        // "conformer_1"/"conformer_2" are the ARCHIVE'S deposited labels
        // (auto-numbered basins); the page renders them as "Conformer
        // Group N" -- see `conformerLabel` (domain/conformerEvidence.ts).
        const conformerOne = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        const conformerTwo = screen.getByText("Conformer Group 2").closest(".conformer-card") as HTMLElement
        expect(within(conformerOne).getByRole("button", { name: /Conformer Group 1/ })).toHaveAttribute("aria-pressed", "true")
        expect(within(conformerTwo).getByRole("button", { name: /Conformer Group 2/ })).toHaveAttribute("aria-pressed", "false")
        // Counts stay distinct: observations, calculation rows (with their
        // own opt/freq/sp breakdown), and coverage are three DIFFERENT
        // units, never conflated -- checked on BOTH cards, not just the
        // first (a mutation reading every card's coverage off
        // conformers[0] would still pass a first-card-only check). The
        // breakdown (3 opt/2 freq/2 sp on card one) is the raw calculation
        // count per stage; the coverage line below it (2/2 obs, a
        // DIFFERENT number) is how many OBSERVATIONS have that stage --
        // labeling both as "obs" makes the coverage line's unit explicit,
        // never letting a reader sum it against calculation rows.
        expect(within(conformerOne).getByText("2 observations · 7 calculation rows (3 opt · 2 freq · 2 sp)")).toBeVisible()
        expect(within(conformerOne).getByText("opt 2/2 obs · freq 2/2 obs · sp 1/2 obs")).toBeVisible()
        expect(within(conformerTwo).getByText("1 observation · 3 calculation rows (1 opt · 1 freq · 1 sp)")).toBeVisible()
        expect(within(conformerTwo).getByText("opt 1/1 obs · freq 1/1 obs · sp 1/1 obs")).toBeVisible()
        // The URL becomes addressable for the default selection (reload survives it).
        expect(new URLSearchParams(window.location.search).get("conformer")).toBe(groupOneRef)
    })

    it("renders the evidence-linkage panel under the picker, scoped to the SELECTED conformer, and updates it when the selection changes", async () => {
        // No page-level test exercised this panel before -- removing the
        // `<ConformerEvidenceLinkage>` line from `SpeciesEntryPage.tsx`, or
        // wiring it to the wrong conformer, passed every other test. This
        // is that coverage: the panel must be present under the picker,
        // must reflect conformer ONE's own numbers by default, and must
        // switch to conformer TWO's own (different) numbers once selected
        // -- never staying pinned to whichever conformer loaded first.
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")

        const panelHeading = () => screen.getByRole("heading", { name: /^Evidence for / })
        const stepFor = (kind: "observations" | "calculations" | "geometries") =>
            document.querySelector(`[data-linkage-step="${kind}"]`) as HTMLElement
        // The step figures live inside a collapsed-by-default `<details>`
        // (the "mechanics", one click away from the prose story) -- open it
        // once; it stays open across the conformer switch below since
        // re-selecting a conformer re-renders the panel's CONTENT, not the
        // `<details>` element's own open/closed state.
        const openMechanics = () => user.click(screen.getByText(/^How this evidence connects/))

        expect(panelHeading()).toHaveTextContent("Evidence for Conformer Group 1")
        await openMechanics()
        expect(within(stepFor("observations")).getByText("2")).toBeVisible()
        expect(within(stepFor("calculations")).getByText("7")).toBeVisible()
        expect(within(stepFor("geometries")).getByText("2")).toBeVisible()

        await user.click(within(screen.getByText("Conformer Group 2").closest(".conformer-card") as HTMLElement)
            .getByRole("button", { name: /Conformer Group 2/ }))

        expect(await screen.findByRole("heading", { name: "Evidence for Conformer Group 2" })).toBeVisible()
        const details = screen.getByText(/^How this evidence connects/).closest("details") as HTMLDetailsElement
        if (!details.open) await openMechanics()
        expect(within(stepFor("observations")).getByText("1")).toBeVisible()
        expect(within(stepFor("calculations")).getByText("3")).toBeVisible()
        expect(within(stepFor("geometries")).getByText("1")).toBeVisible()
    })

    it("collapses each conformer card's own ref behind a References disclosure, keeping the basin label visible outside it", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        const conformerOne = screen.getByText("Conformer Group 1").closest(".conformer-card") as HTMLElement
        expect(within(conformerOne).getByText("Conformer Group 1")).toBeVisible()
        expect(within(conformerOne).getByText("References (1)")).toBeInTheDocument()
        expect(within(conformerOne).queryByText(groupOneRef)).not.toBeVisible()
        await user.click(within(conformerOne).getByText("References (1)"))
        expect(within(conformerOne).getByRole("link", { name: groupOneRef })).toBeVisible()
    })

    it("states honestly when the entry has no projected conformer basins, while entry-level tabs still work", async () => {
        const user = userEvent.setup()
        server.use(...handlers({ noConformers: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        expect(await screen.findByRole("heading", { name: "Conformers" })).toBeVisible()
        expect(screen.getByText("No conformer basins are projected for this entry.")).toBeVisible()
        expect(screen.getByText("No conformer basins are projected for this entry, so there is no geometry evidence to show.")).toBeVisible()
        // Thermo/statmech/transport are entry-scoped lists, independent of
        // whether any conformer basin is projected -- they still render.
        await user.click(screen.getByRole("tab", { name: "Thermochemistry" }))
        expect(await screen.findByText("thm_one")).toBeVisible()
        expect(screen.getByText("thm_two")).toBeVisible()
    })

    it("uses a neutral 'Conformer' heading, not the imperative 'Choose a conformer', when there is exactly one basin", async () => {
        server.use(...handlers({ singleConformer: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        expect(await screen.findByRole("heading", { name: "Conformer" })).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Choose a conformer" })).not.toBeInTheDocument()
        expect(screen.getByText("This entry has one deposited conformer basin. Its evidence is shown below.")).toBeVisible()
    })
})

describe("species-entry page: tabs are a real, keyboard-operable ARIA tablist", () => {
    it("has tablist/tab/tabpanel roles wired together (including aria-controls), and ArrowRight moves focus without stealing selection", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        const tablist = screen.getByRole("tablist", { name: "Conformer evidence" })
        const geometryTab = within(tablist).getByRole("tab", { name: "Geometry" })
        const spTab = within(tablist).getByRole("tab", { name: "Single-point energy" })
        expect(geometryTab).toHaveAttribute("aria-selected", "true")
        expect(geometryTab).toHaveAttribute("aria-controls", "panel-geometry")
        expect(spTab).toHaveAttribute("aria-controls", "panel-sp")
        expect(geometryTab).toHaveAttribute("tabIndex", "0")
        expect(spTab).toHaveAttribute("tabIndex", "-1")
        expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", geometryTab.id)
        expect(screen.getByRole("tabpanel")).toHaveAttribute("id", "panel-geometry")

        geometryTab.focus()
        await user.keyboard("{ArrowRight}")
        expect(document.activeElement).toBe(spTab)
        // Focus moved; selection (the active tab/panel) did not change yet --
        // manual-activation pattern.
        expect(geometryTab).toHaveAttribute("aria-selected", "true")
    })

    it("never renders one tab's content on another tab (thermo and statmech panels stay distinct)", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")

        await user.click(screen.getByRole("tab", { name: "Thermochemistry" }))
        expect(await within(screen.getByRole("tabpanel")).findByText("thm_one")).toBeVisible()
        // Only one tabpanel is ever mounted at a time -- the statmech
        // record's own heading text must not appear on the thermo panel.
        expect(within(screen.getByRole("tabpanel")).queryByText("Statistical mechanics")).not.toBeInTheDocument()

        await user.click(screen.getByRole("tab", { name: "Statistical mechanics" }))
        // "sm_1" also appears (not visibly) inside the pre-existing
        // "Conformer context" disclosure this same statmech list already
        // renders -- match on the visible record card's own `<code>` ref.
        const smRefs = await within(screen.getByRole("tabpanel")).findAllByText("sm_1")
        expect(smRefs.some((node) => node.tagName === "CODE")).toBe(true)
        expect(within(screen.getByRole("tabpanel")).queryByText("thm_one")).not.toBeInTheDocument()
        expect(within(screen.getByRole("tabpanel")).queryByText("Thermochemistry")).not.toBeInTheDocument()
    })

    it("shows the live transport empty state, not a hypothetical placeholder", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        await user.click(screen.getByRole("tab", { name: "Transport" }))
        expect(await screen.findByText(
            "No transport records are deposited for this entry. This is the archive's own answer — not a failed request — so nothing further will load if you retry.",
        )).toBeVisible()
    })
})

describe("species-entry page: selecting a conformer scopes geometry, single-point, thermo and statmech to it", () => {
    it("switches Geometry/SP tab content to the newly selected conformer's own evidence, and the choice survives via the URL", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        expect(await screen.findByRole("link", { name: "geom_g1" })).toBeVisible()
        expect(screen.queryByText("geom_g3")).not.toBeInTheDocument()

        await user.click(within(screen.getByText("Conformer Group 2").closest(".conformer-card") as HTMLElement).getByRole("button", { name: /Conformer Group 2/ }))
        expect(await screen.findByRole("link", { name: "geom_g3" })).toBeVisible()
        expect(screen.queryByText("geom_g1")).not.toBeInTheDocument()
        expect(new URLSearchParams(window.location.search).get("conformer")).toBe(groupTwoRef)

        await user.click(screen.getByRole("tab", { name: "Single-point energy" }))
        expect(await screen.findByRole("link", { name: "calc_sp_3" })).toBeVisible()
        // The conformer choice is still the query param after a tab switch.
        expect(new URLSearchParams(window.location.search).get("conformer")).toBe(groupTwoRef)
    })

    it("lists every observation on the Single-point tab -- including one with TWO sp calculations and one with none", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}/sp`)
        render(<App />)
        expect(await screen.findByRole("link", { name: "co_1" })).toBeVisible()
        expect(screen.getByRole("link", { name: "co_2" })).toBeVisible()
        // co_1 has two sp calculations -- both must render, not just the first.
        expect(screen.getByRole("link", { name: "calc_sp_1" })).toBeVisible()
        expect(screen.getByRole("link", { name: "calc_sp_1b" })).toBeVisible()
        expect(screen.getByText("No single-point calculation recorded for this observation.")).toBeVisible()
    })

    it("shows how many optimization calculations each observation has, on the Geometry tab", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        // co_1 has TWO opt calculations (calc_opt_1a, calc_opt_1b); co_2 has one.
        expect(await screen.findByText((_, element) => (
            element?.tagName === "LI" && element.textContent === "co_1 — 2 optimization calculations"
        ))).toBeVisible()
        expect(screen.getByText((_, element) => (
            element?.tagName === "LI" && element.textContent === "co_2 — 1 optimization calculation"
        ))).toBeVisible()
    })

    it("renders thermo three ways: this conformer, the OTHER named conformer, and no conformer link -- never as an error", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}/thermo`)
        render(<App />)
        expect(await screen.findByText("thm_one")).toBeVisible()
        expect(screen.getByText("thm_two")).toBeVisible()
        // thm_three is linked to a DIFFERENT conformer than the one
        // selected -- present (never deleted) but demoted into the
        // collapsed "other conformers" disclosure, so it's in the document
        // without being visible until opened.
        expect(screen.getByText("thm_three")).toBeInTheDocument()
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()

        const otherDetails = document.querySelector(".conformer-attribution-other") as HTMLDetailsElement
        expect(otherDetails.open).toBe(false)
        fireEvent.click(within(otherDetails).getByText(/records? from other conformers/))

        const groupHeadings = screen.getAllByRole("heading", { level: 3 })
            .filter((node) => node.className === "conformer-evidence-group-heading")
        expect(groupHeadings.map((node) => node.textContent)).toEqual(["From Conformer Group 1", "From Conformer Group 2", "No conformer link"])

        const fromOne = groupHeadings[0].closest(".conformer-evidence-group") as HTMLElement
        const fromTwo = groupHeadings[1].closest(".conformer-evidence-group") as HTMLElement
        const noLink = groupHeadings[2].closest(".conformer-evidence-group") as HTMLElement
        expect(within(fromOne).getByText("thm_one")).toBeVisible()
        expect(within(fromOne).queryByText("thm_two")).not.toBeInTheDocument()
        expect(within(fromOne).queryByText("thm_three")).not.toBeInTheDocument()
        // thm_three is linked to Conformer Group 2, NOT the selected Conformer Group 1
        // -- it must land under its own named group, never under "No
        // conformer link" (the exact regression this replaced).
        expect(within(fromTwo).getByText("thm_three")).toBeVisible()
        expect(within(fromTwo).queryByText("thm_one")).not.toBeInTheDocument()
        expect(within(fromTwo).queryByText("thm_two")).not.toBeInTheDocument()
        expect(within(noLink).getByText("thm_two")).toBeVisible()
        expect(within(noLink).queryByText("thm_one")).not.toBeInTheDocument()
        expect(within(noLink).queryByText("thm_three")).not.toBeInTheDocument()
    })

    it("renders the evidence-completeness rubric as a compact score plus chips for only the FAILED checks, not eight prose lines", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}/thermo`)
        render(<App />)
        await screen.findByText("thm_one")
        // thm_one: score 6/8, two false checks (uncertainty, SCF stability).
        expect(screen.getByText("Evidence completeness (6 / 8)")).toBeVisible()
        expect(screen.getByText("uncertainty")).toBeVisible()
        expect(screen.getByText("SCF stability check")).toBeVisible()
        // The six PASSING checks are not enumerated by default -- they
        // still exist inside the full-checklist disclosure (collapsed), so
        // this checks visibility, not absence from the DOM.
        const sourceCalcsRow = screen.getByText((_, element) => (
            element?.tagName === "LI" && element.textContent === "Present — source calculations"
        ))
        expect(sourceCalcsRow).not.toBeVisible()
        // thm_three: every check true -- reads as fully satisfied, no chips.
        // It's linked to a different conformer than the one selected, so it
        // sits inside the collapsed "other conformers" disclosure -- open
        // it before checking visibility.
        fireEvent.click(screen.getByText(/records? from other conformers/))
        expect(screen.getByText("Every evidence-completeness check is satisfied.")).toBeVisible()
        // The full checklist stays reachable behind its own disclosure.
        expect(screen.getAllByText(/^Full checklist \(/).length).toBeGreaterThan(0)
    })

    it("renders statmech the same three ways, using its real include=conformers link -- sm_1 this conformer, sm_3 the other, sm_2 no link, never swapped", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}/statmech`)
        render(<App />)
        // The flat (ungrouped) list renders immediately while the
        // include=conformers refetch is in flight -- wait for the
        // ATTRIBUTED groups specifically, not just any "sm_1" text.
        await screen.findByRole("heading", { name: "From Conformer Group 1" })
        const groupHeadings = screen.getAllByRole("heading", { level: 3 })
            .filter((node) => node.className === "conformer-evidence-group-heading")
        expect(groupHeadings.map((node) => node.textContent)).toEqual(["From Conformer Group 1", "From Conformer Group 2", "No conformer link"])

        const fromOne = groupHeadings[0].closest(".conformer-evidence-group") as HTMLElement
        const fromTwo = groupHeadings[1].closest(".conformer-evidence-group") as HTMLElement
        const noLink = groupHeadings[2].closest(".conformer-evidence-group") as HTMLElement
        expect(within(fromOne).getAllByText("sm_1").some((node) => node.tagName === "CODE")).toBe(true)
        expect(within(fromOne).queryByText("sm_2")).not.toBeInTheDocument()
        expect(within(fromOne).queryByText("sm_3")).not.toBeInTheDocument()
        expect(within(fromTwo).getAllByText("sm_3").some((node) => node.tagName === "CODE")).toBe(true)
        expect(within(fromTwo).queryByText("sm_1")).not.toBeInTheDocument()
        expect(within(noLink).getAllByText("sm_2").some((node) => node.tagName === "CODE")).toBe(true)
        expect(within(noLink).queryByText("sm_1")).not.toBeInTheDocument()
        expect(within(noLink).queryByText("sm_3")).not.toBeInTheDocument()
    })

    it("shows every statmech record ungrouped, with a status line, when the conformers include fails -- never zero records for a nonzero count", async () => {
        // Probed shape from the review: base list 200s (3 records), the
        // include=conformers refetch 503s. The count line must still say 3
        // records, and all 3 must still render.
        server.use(...handlers({ statmechConformersIncludeFails: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}/statmech`)
        render(<App />)
        // The base list resolves independently of (and faster than) the
        // include=conformers refetch -- wait for the ERROR-specific text,
        // not the count line, which would pass even mid-flight.
        expect(await screen.findByText(
            /Showing every record for this entry, ungrouped, until the conformer link resolves\./,
        )).toBeVisible()
        expect(screen.getByText((_, element) => element?.textContent === "3 records · review: 3 not reviewed")).toBeVisible()
        expect(screen.getAllByText("sm_1").some((node) => node.tagName === "CODE")).toBe(true)
        expect(screen.getAllByText("sm_2").some((node) => node.tagName === "CODE")).toBe(true)
        expect(screen.getAllByText("sm_3").some((node) => node.tagName === "CODE")).toBe(true)
    })
})
