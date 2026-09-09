import { StrictMode } from "react"
import { delay, http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import App from "./App"
import { bySummaryText } from "./test/disclosureQueries"

const speciesRef = "spc_abcde234567abcde234567abcd"
const speciesRefTwo = "spc_bcdef234567bcdef234567abcde"
const entryRef = "spe_cdefg234567cdefg234567abcd"
const server = setupServer()

function overviewSpecies(ref = speciesRef) {
    return {
        species_ref: ref,
        canonical_smiles: "[OH2]",
        inchi_key: "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
        formula: "H2O",
        charge: 0,
        multiplicity: 1,
        stereo_kind: "achiral",
        entries: [{
            species_entry_ref: entryRef,
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            review: { status: "not_reviewed" },
            availability: {
                has_thermo: true,
                has_statmech: true,
                has_transport: false,
                has_conformers: true,
                calculation_count: 14,
            },
        }],
    }
}
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

// `BrowsePage` mounts `BrowseFilterForm`, which fires several unscoped
// `/meta/*` vocabulary requests unconditionally regardless of which kind is
// selected (see `BrowsePage.test.tsx`'s own `emptyVocabHandlers`, the same
// need here) -- without handlers for these, this file's `onUnhandledRequest:
// "error"` server fails every browse-route test for a reason unrelated to
// what it actually asserts. Empty results are enough.
function emptyVocabHandlers() {
    return ["methods", "basis-sets", "software", "workflow-tools", "software-versions", "workflow-tool-versions", "reaction-families"].map((path) =>
        http.get(`/api/v1/scientific/meta/${path}`, () => HttpResponse.json({ results: [] })))
}

function emptyBrowseEnvelope() {
    return { records: [], pagination: { offset: 0, limit: 20, returned: 0, total: 0, post_collapse_total: 0 } }
}

describe("public archive shell", () => {
    it("renders visible keyboard navigation and excludes the admin diagnostic route", async () => {
        const user = userEvent.setup(); render(<App />)
        expect(await screen.findByRole("heading", { name: "TCKDB" })).toBeVisible()
        await user.tab(); expect(screen.getByText("Skip to content")).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "TCKDB home" })).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "Species" })).toHaveFocus()
        expect(screen.queryByText(/Machine-Review Inspection/)).not.toBeInTheDocument()
    })

    it("offers a visible accessible Formula/SMILES choice for ambiguous input", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({
            records: [{ species_ref: speciesRef, formula: "Cl", canonical_smiles: "[Cl]", charge: 0, multiplicity: 2, entries: [] }],
        })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "Cl")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("group", { name: /Search “Cl” as/ })).toBeVisible()
        await user.click(screen.getByRole("button", { name: "Formula" }))
        // The result reads as chemistry (SMILES leads, formula follows in
        // parentheses), not as the raw ref -- the ref stays present, but
        // demoted, alongside it.
        const result = await screen.findByRole("link", { name: /^\[Cl\] \(Cl\)/ })
        expect(result).toBeVisible()
        expect(result).toHaveAttribute("href", `/species/${speciesRef}`)
        expect(screen.getByText(speciesRef)).toBeVisible()
    })

    it("completes a StrictMode search and clears its loading state", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({
            records: [{ species_ref: speciesRef, formula: "H2O", canonical_smiles: "O", charge: 0, multiplicity: 1, entries: [] }],
        })))
        const user = userEvent.setup(); render(<StrictMode><App /></StrictMode>)
        await user.type(await screen.findByLabelText("Exact species identifier"), "H2O")
        const button = screen.getByRole("button", { name: "Search" })
        await user.click(button)
        expect(await screen.findByRole("link", { name: /^O \(H2O\)/})).toBeVisible()
        expect(screen.getByText(speciesRef)).toBeVisible()
        expect(button).toHaveAttribute("aria-busy", "false")
    })

    it("routes spc references to species even when entries are returned", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => (
            HttpResponse.json({ records: [overviewSpecies()] })
        )))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), speciesRef)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("heading", { name: "H2O" })).toBeVisible()
        expect(screen.getByText(speciesRef)).toBeVisible()
        // The lone entry's group heading already says "ground electronic
        // state" -- the card itself drops the redundant bare state phrase
        // (see `domain/recordFacets.test.ts`'s `includeState` tests).
        expect(screen.getByRole("link", { name: "minimum" }))
            .toHaveAttribute("href", `/species-entries/${entryRef}`)
    })

    it("routes spe references to the precise entry", async () => {
        const entry = {
            species_entry_ref: entryRef,
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            review: { status: "not_reviewed" },
            availability: {
                has_thermo: false,
                has_statmech: false,
                has_transport: false,
                has_conformers: false,
                calculation_count: 0,
            },
        }
        const species = {
            species_ref: speciesRef,
            canonical_smiles: "O",
            inchi_key: "X",
            formula: "O",
            charge: 0,
            multiplicity: 1,
            entries: [entry],
        }
        server.use(
            http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [species] })),
            http.get("/api/v1/scientific/conformers/search", () => HttpResponse.json({ records: [] })),
        )
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), entryRef)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("heading", { name: "O" })).toBeVisible()
        // The entry's OWN ref is visible at rest, first in the identity
        // block -- never behind the collapsed References disclosure (owner
        // decision: "yes show each record's own ref inline"). Only the
        // RELATED parent-species ref lives in that disclosure now (1, not
        // 2/3) -- InChIKey moved to the always-visible chemistry-
        // identifiers row above, alongside SMILES.
        expect(screen.getByText(entryRef)).toBeVisible()
        await user.click(screen.getByText(bySummaryText("References (1)")))
        expect(screen.getByText(speciesRef)).toBeVisible()
    })

    it("renders formula search results as species-grain Links and follows one", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            const query = new URL(request.url).searchParams
            if (query.has("species_ref")) return HttpResponse.json({ records: [overviewSpecies()] })
            return HttpResponse.json({ records: [
                {
                    species_ref: speciesRef, formula: "H2O", canonical_smiles: "O", charge: 0, multiplicity: 1,
                    entries: [{ species_entry_ref: entryRef }],
                },
                {
                    species_ref: speciesRefTwo, formula: "H2O2", canonical_smiles: "OO", charge: 0, multiplicity: 1,
                    entries: [],
                },
            ] })
        }))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "H2O")
        await user.click(screen.getByRole("button", { name: "Search" }))
        // Each row reads by its own chemistry, not by an interchangeable ref:
        // the two matches share the same formula prefix but diverge past it,
        // and each keeps its own entry count ("1 entry" vs "0 entries").
        const result = await screen.findByRole("link", { name: /^O \(H2O\) charge 0 · spin singlet \(1\) · 1 entry$/ })
        expect(result).toHaveAttribute("href", `/species/${speciesRef}`)
        const other = screen.getByRole("link", { name: /^OO \(H2O2\) charge 0 · spin singlet \(1\) · 0 entries$/ })
        expect(other).toBeVisible()
        expect(other).toHaveAttribute("href", `/species/${speciesRefTwo}`)
        // The ref stays present and copyable alongside the chemistry, just demoted.
        expect(screen.getByText(speciesRef)).toBeVisible()
        expect(screen.getByText(speciesRefTwo)).toBeVisible()
        await user.click(result)
        expect(await screen.findByRole("heading", { name: "H2O" })).toBeVisible()
    })

    it("keeps structure search at entry grain and shows SMILES when formula is unavailable", async () => {
        server.use(
            http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({
                records: [{ species_ref: speciesRef, species_entry_ref: entryRef, smiles: "CCO", charge: 0, multiplicity: 1 }],
            })),
            // Species mode no longer fires a reaction-participation lookup on
            // its own (that used to be automatic; it is now the explicit
            // "Also search reactions involving …" cross-link, reaction mode's
            // job) -- no `/reactions/browse` handler is registered here, and
            // this file's server is `onUnhandledRequest: "error"`, so a
            // regression that brought the old automatic fetch back would fail
            // this test for that reason alone.
        )
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "smiles:CCO")
        await user.click(screen.getByRole("button", { name: "Search" }))
        // The structure-search endpoint never returns a formula (#251): the
        // row leads with SMILES instead and says so honestly, rather than
        // leaving a blank or falling back to the ref.
        expect(screen.getByText("formula not available")).toBeVisible()
        const result = await screen.findByRole("link", { name: /^CCO formula not available/ })
        expect(result).toHaveAttribute("href", `/species-entries/${entryRef}`)
        expect(screen.getByText(entryRef)).toBeVisible()
    })

    it("keeps only the latest search result and does not navigate after unmount", async () => {
        server.use(http.get("/api/v1/scientific/species/search", async ({ request }) => {
            const formula = new URL(request.url).searchParams.get("formula")
            if (formula === "H2O") await delay(40)
            const ref = formula === "H2O" ? speciesRef : speciesRefTwo
            const chemistry = formula === "H2O"
                ? { formula: "H2O", canonical_smiles: "O" }
                : { formula: "H2", canonical_smiles: "[H][H]" }
            return HttpResponse.json({ records: [{ species_ref: ref, ...chemistry, charge: 0, multiplicity: 1, entries: [] }] })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        await user.type(input, "H2O"); await user.click(screen.getByRole("button", { name: "Search" }))
        await user.clear(input); await user.type(input, "H2"); await user.click(screen.getByRole("button", { name: "Search" }))
        // The stale, slower "H2O" response must never overwrite the "H2" result.
        expect(await screen.findByRole("link", { name: /^\[H\]\[H\] \(H2\)/ })).toBeVisible()
        expect(screen.queryByRole("link", { name: /^O \(H2O\)/ })).not.toBeInTheDocument()
        cleanup(); await delay(60); expect(window.location.pathname).toBe("/")
    })

    it("renders empty, malformed-success, and HTTP-error states with idle busy state", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            const formula = new URL(request.url).searchParams.get("formula")
            if (formula === "H2O") return HttpResponse.json({ records: [] })
            if (formula === "Ca") return HttpResponse.json({ records: [{ species_ref: speciesRef }] })
            return HttpResponse.json({ detail: "archive unavailable" }, { status: 503 })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        const button = screen.getByRole("button", { name: "Search" })
        await user.type(input, "H2O"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("No exact formula record")
        expect(button).toHaveAttribute("aria-busy", "false")
        await user.clear(input); await user.type(input, "Ca"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("could not complete")
        expect(button).toHaveAttribute("aria-busy", "false")
        await user.clear(input); await user.type(input, "H2"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("could not complete")
        expect(button).toHaveAttribute("aria-busy", "false")
    })

    it("clears a slow valid request synchronously for invalid and ambiguous submits", async () => {
        server.use(http.get("/api/v1/scientific/species/search", async () => {
            await delay(50)
            return HttpResponse.json({ records: [{ species_ref: speciesRef, entries: [] }] })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        const button = screen.getByRole("button", { name: "Search" })
        await user.type(input, "H2O"); await user.click(button)
        await user.clear(input); await user.type(input, "spc_BAD"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("26 lowercase base32")
        expect(button).toHaveAttribute("aria-busy", "false")
        await user.clear(input); await user.type(input, "H2O"); await user.click(button)
        await user.clear(input); await user.type(input, "Cl"); await user.click(button)
        expect(await screen.findByRole("group", { name: /Search “Cl” as/ })).toBeVisible()
        expect(button).toHaveAttribute("aria-busy", "false")
        // The ref is checked directly (not via an exact link name) because
        // the row's accessible name is now the chemistry, not the ref --
        // checking for the ref's continued *absence* still needs to survive
        // that, since it stands in for "did the stale slow response render".
        await delay(60); expect(screen.queryByText(speciesRef)).not.toBeInTheDocument()
    })

    it("does not allow an ambiguity chooser to search stale textbox content", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            expect(new URL(request.url).searchParams.get("formula")).toBe("Br")
            return HttpResponse.json({
                records: [{ species_ref: speciesRef, formula: "Br", canonical_smiles: "[Br]", charge: 0, multiplicity: 2, entries: [] }],
            })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        await user.type(input, "Cl"); await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("button", { name: "Formula" })).toBeVisible()
        await user.clear(input); await user.type(input, "Br")
        expect(screen.queryByRole("button", { name: "Formula" })).not.toBeInTheDocument()
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("group", { name: /Search “Br” as/ })).toBeVisible()
        await user.click(screen.getByRole("button", { name: "Formula" }))
        const result = await screen.findByRole("link", { name: /^\[Br\] \(Br\)/ })
        expect(result).toBeVisible()
        expect(screen.getByText(speciesRef)).toBeVisible()
    })
})

const publicRoutes: Array<[path: string, heading: string, ref?: string]> = [
    // Each browse kind now names itself in its own h1 (`BROWSE_KIND_CONTENT`,
    // `api/browseApi.ts`) -- was the SAME "Browse the archive" on all four
    // kind paths, the owner's own repeated complaint ("Why is the browse
    // reactions with the species/transition/vanderwaals browsing page?").
    ["/species", "Browse species", undefined],
    ["/species/spc_abcde234567abcde234567abcd", "H2O", speciesRef],
    // The h1 states what the record IS ("Conformer basin"), not the
    // producer's own deposited label -- see `ConformerGroupPage.tsx`'s own
    // header comment. The fixture below still sets `label: "Conformer
    // group"` deliberately, so this heading value only changes because the
    // page itself no longer titles by that label at all.
    ["/conformer-groups/cfg_abc", "Conformer basin", "cfg_abc"],
    ["/conformer-observations/cfo_abc", "Computed observation", "cfo_abc"],
    ["/calculations/calc_abc", "Single-point of H2O", "calc_abc"],
    ["/geometries/geom_abc", "Geometry", "geom_abc"],
    // `/reactions` is no longer `RecordPlaceholderPage` -- it is `BrowsePage`
    // with the "reaction" kind fixed by the route (same component as
    // `/species`, `/vdw-complexes`, `/transition-states`). Its heading IS a
    // fixed, per-kind string now ("Browse reactions", `BROWSE_KIND_CONTENT`)
    // -- it does not use this table because rendering it needs its own
    // `/reactions/browse` mock, which this table's own per-path handler
    // setup below does not provide (only `/species/*`/`/conformer-*`/
    // `/calculations/*`/`/geometries/*` get one). It gets its own dedicated
    // test below instead, the same treatment `/reactions/:reactionRef`
    // already got when THAT stopped being `RecordPlaceholderPage`.
    // `/reactions/:reactionRef` is a real chooser page now (`ReactionOverviewPage`,
    // not `RecordPlaceholderPage`) -- its own dedicated test below builds a
    // realistic `reactions/search` fixture and asserts the rendered
    // equation heading, rather than forcing it through this shared table's
    // generic `heading`/`ref` string-equality checks (which assumed the
    // old placeholder's fixed "Reaction" title).
    //
    // `/methods` is no longer `RecordPlaceholderPage` either (methods-surface
    // plan PR 3) -- it is `MethodsIndexPage`, which fires its own three
    // requests (`level-of-theories/browse`, `meta/software`,
    // `meta/workflow-tools`) this shared table's per-path handler setup
    // does not provide. Same treatment as `/reactions` above: its own
    // dedicated test below instead of a row in this table.
]

describe.each(publicRoutes)("route shell %s", (path, heading, ref) => {
    it("renders the declared public route deterministically", async () => {
        if (path === "/species") {
            server.use(http.get("/api/v1/scientific/species/browse", () => HttpResponse.json({
                records: [], pagination: { offset: 0, limit: 20, returned: 0, total: 0, post_collapse_total: 0 },
            })))
        }
        if (path.startsWith("/species/")) {
            server.use(http.get("/api/v1/scientific/species/search", () => (
                HttpResponse.json({ records: [overviewSpecies()] })
            )))
        }
        if (path.startsWith("/conformer-groups/")) {
            server.use(http.get("/api/v1/scientific/conformer-groups/:ref", () => HttpResponse.json({
                record: {
                    conformer_group: {
                        conformer_group_ref: "cfg_abc", label: "Conformer group",
                        review: { status: "not_reviewed" },
                    },
                    species: { species_ref: speciesRef, species_entry_ref: entryRef },
                    observations_summary: { total: 0, by_scientific_origin: {} },
                    evidence_summary: {
                        calculation_count: 0, optimization_chain_count: 0, geometry_count: 0,
                        evidence_coverage: { opt: 0, freq: 0, sp: 0 },
                    },
                    observations: [], calculations: [], geometries: [],
                },
            })))
        }
        if (path.startsWith("/conformer-observations/")) {
            server.use(http.get("/api/v1/scientific/conformer-observations/:ref", () => HttpResponse.json({
                record: {
                    conformer_observation: {
                        conformer_observation_ref: "cfo_abc",
                        scientific_origin: "computed",
                        review: { status: "not_reviewed" },
                    },
                    conformer_group: {
                        conformer_group_ref: "cfg_abc", label: "Conformer group",
                        review: { status: "not_reviewed" },
                    },
                    species: { species_ref: speciesRef, species_entry_ref: entryRef },
                    assignment_scheme: null,
                    evidence_summary: {
                        calculation_count: 0, geometry_count: 0, has_opt: false, has_freq: false,
                        has_sp: false, has_geometry_validation: false, has_scf_stability: false,
                        levels_of_theory: {},
                    },
                    available_sections: {
                        has_observations: false, has_selections: false, has_calculations: false,
                        has_geometries: false, has_review: false,
                    },
                    observations: [], selections: [], calculations: [], geometries: [], review_history: [],
                },
            })))
        }
        if (path.startsWith("/calculations/")) {
            server.use(http.get("/api/v1/scientific/calculations/:ref", () => HttpResponse.json({
                record: {
                    calculation: {
                        calculation_ref: "calc_abc", type: "sp", quality: "raw",
                        created_at: "2026-07-21T12:06:50.748258",
                        review: { status: "not_reviewed" },
                    },
                    owner: {
                        kind: "species_entry",
                        species_entry: {
                            species_ref: speciesRef, species_entry_ref: entryRef,
                            formula: "H2O",
                            canonical_smiles: "[OH2]", inchi_key: "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
                            charge: 0, multiplicity: 1,
                            species_entry_kind: "minimum", electronic_state_kind: "ground",
                        },
                        transition_state_entry: null,
                    },
                    level_of_theory: null, software_release: null, workflow_tool_release: null, literature: null,
                    provenance: {
                        has_result: false, converged: null,
                        geometry_validation_status: "not_present", scf_stability_status: "not_present",
                        submission_ref: null,
                    },
                    available_sections: {
                        has_results: false, has_dependencies: false, has_parameters: false,
                        has_constraints: false, has_artifacts: false, has_input_geometries: false,
                        has_output_geometries: false, has_geometry_validation: false, has_scf_stability: false,
                        has_wavefunction_diagnostic: false, has_spin_diagnostic: false, has_freq_modes: false,
                        has_hessian: false, has_scan: false, has_irc: false, has_path_search: false,
                        has_execution_environment: false, has_energy_corrections: false,
                    },
                    results: null, dependencies: [], review_history: [],
                    input_geometries: [], output_geometries: [],
                },
            })))
        }
        if (path.startsWith("/geometries/")) {
            server.use(http.get("/api/v1/scientific/geometries/:ref", () => HttpResponse.json({
                geometry_ref: "geom_abc",
                natoms: 0,
                geom_hash: "hash_abc",
                format: "cartesian",
                coordinate_units: "angstrom",
                symbols: [],
                coords: [],
                atoms: [],
                xyz_text: null,
                created_at: "2026-07-21T12:06:50.748258",
                provenance: { produced_by: [], used_as_input_by: [] },
            })))
        }
        window.history.replaceState({}, "", path); render(<App />)
        expect(await screen.findByRole("heading", { name: heading })).toBeVisible()
        // Route ordering (item 1 of the per-kind browse paths): a heading
        // match alone does not prove the URL actually resolved to THIS
        // route rather than some other one that happens to render the same
        // text -- assert the address bar too.
        expect(window.location.pathname).toBe(path)
        // The calculation page's own ref lives inside the collapsed
        // References disclosure (item 3 of the calculation-page rework) --
        // present in the DOM, but not VISIBLE until opened.
        if (path.startsWith("/calculations/")) {
            const summary = screen.getByText(bySummaryText(/References \(/))
            summary.closest("details")?.setAttribute("open", "")
        }
        if (ref) expect(screen.getByText(ref)).toBeVisible()
        if (path.includes("/species-entries/") && path.split("/").length === 4) {
            expect(screen.getByText(path.split("/").at(-1) ?? "", { selector: "code" })).toBeVisible()
        }
    })
})

// The site nav's "Reactions" link used to be a dead end -- `/reactions`
// rendered `RecordPlaceholderPage`, not the working reaction browse (only
// reachable via `/species?kind=reaction`). It is `BrowsePage` now, same
// component as `/species`/`/vdw-complexes`/`/transition-states`, with the
// "reaction" kind fixed by the route -- so it gets its own test (per the
// comment on its removal from `publicRoutes` above), not the generic
// `heading`/`ref` table.
describe("browse kind paths: each of the four renders BrowsePage with its own kind, its own heading", () => {
    it("/reactions renders BrowsePage with the reaction kind's own heading and hits /reactions/browse, not /species/browse", async () => {
        let speciesCalls = 0
        server.use(
            ...emptyVocabHandlers(),
            http.get("/api/v1/scientific/species/browse", () => { speciesCalls += 1; return HttpResponse.json(emptyBrowseEnvelope()) }),
            http.get("/api/v1/scientific/reactions/browse", () => HttpResponse.json(emptyBrowseEnvelope())),
        )
        window.history.replaceState({}, "", "/reactions")
        render(<App />)
        // Each kind names itself now (`BROWSE_KIND_CONTENT`) -- was the
        // SAME "Browse the archive" heading on all four kind paths.
        expect(await screen.findByRole("heading", { name: "Browse reactions" })).toBeVisible()
        expect(window.location.pathname).toBe("/reactions")
        expect(speciesCalls).toBe(0)
    })

    it.each([
        ["/vdw-complexes", "Browse van der Waals complexes"],
        ["/transition-states", "Browse transition states"],
    ])("%s renders BrowsePage with its own heading, '%s'", async (path, heading) => {
        server.use(
            ...emptyVocabHandlers(),
            http.get("/api/v1/scientific/species/browse", () => HttpResponse.json(emptyBrowseEnvelope())),
            http.get("/api/v1/scientific/transition-states/browse", () => HttpResponse.json(emptyBrowseEnvelope())),
        )
        window.history.replaceState({}, "", path)
        render(<App />)
        expect(await screen.findByRole("heading", { name: heading })).toBeVisible()
        expect(window.location.pathname).toBe(path)
    })
})

// `/methods` used to render `RecordPlaceholderPage` (a bare "Methods" h1,
// no data) -- it is `MethodsIndexPage` now (methods-surface plan PR 3),
// so it gets its own dedicated test, same treatment `/reactions` got
// above when IT stopped being the placeholder.
describe("/methods renders the real methods index, not RecordPlaceholderPage", () => {
    it("shows the Methods h1 and a level-of-theory row from level-of-theories/browse", async () => {
        server.use(
            http.get("/api/v1/scientific/level-of-theories/browse", () => HttpResponse.json({
                records: [{
                    level_of_theory: {
                        level_of_theory_ref: "lot_abc", method: "b3lyp", basis: "def2tzvp",
                        dispersion: null, solvent: null, lot_hash: "hash1", created_at: "2026-07-21T11:59:29Z",
                    },
                    evidence_summary: {
                        calculation_usage_count: 416, has_correction_schemes: true,
                        has_frequency_scale_factors: true, distinct_software_count: 1,
                    },
                    available_sections: { has_correction_schemes: true, has_frequency_scale_factors: true, has_used_by: true, has_software: true },
                }],
                pagination: { offset: 0, limit: 200, returned: 1, total: 1 },
            })),
            http.get("/api/v1/scientific/meta/software", () => HttpResponse.json({ results: [{ value: "Gaussian", count: 416 }] })),
            http.get("/api/v1/scientific/meta/workflow-tools", () => HttpResponse.json({ results: [{ value: "ARC", count: 416 }] })),
        )
        window.history.replaceState({}, "", "/methods")
        render(<App />)
        expect(await screen.findByRole("heading", { name: "Methods" })).toBeVisible()
        expect(window.location.pathname).toBe("/methods")
        const lotLink = await screen.findByRole("link", { name: "b3lyp/def2tzvp" })
        expect(lotLink).toHaveAttribute("href", "/methods/lot_abc")
        expect(await screen.findByText("Gaussian")).toBeVisible()
        expect(await screen.findByText("ARC")).toBeVisible()
        // The old placeholder rendered an h1 literally reading "Methods" too,
        // but with no `<code>` ref and none of the vocabulary content this
        // page fetches -- confirming the three fetched rows above render is
        // what proves this is genuinely `MethodsIndexPage`.
    })
})

describe("site nav: the Reactions link is a real page, not a dead end", () => {
    it("Reactions points at /reactions and is not RecordPlaceholderPage", async () => {
        server.use(
            ...emptyVocabHandlers(),
            http.get("/api/v1/scientific/species/browse", () => HttpResponse.json(emptyBrowseEnvelope())),
            http.get("/api/v1/scientific/reactions/browse", () => HttpResponse.json(emptyBrowseEnvelope())),
        )
        const user = userEvent.setup()
        window.history.replaceState({}, "", "/species")
        render(<App />)
        await screen.findByText(/have been deposited in this archive yet/)
        const link = screen.getByRole("link", { name: "Reactions" })
        expect(link).toHaveAttribute("href", "/reactions")
        await user.click(link)
        expect(await screen.findByRole("heading", { name: "Browse reactions" })).toBeVisible()
        expect(window.location.pathname).toBe("/reactions")
        // The old placeholder rendered an h1 literally reading "Reactions" --
        // confirms this is genuinely `BrowsePage`, not that page surviving
        // under a new route.
        expect(screen.queryByRole("heading", { name: "Reactions" })).not.toBeInTheDocument()
    })

    // `NavLink` marks the active link via `aria-current="page"` (see
    // `index.css`'s `nav a[aria-current="page"]` rule) -- it must land on
    // exactly the ONE nav item matching the current path, on every one of
    // the four browse-kind paths (only two of which -- Species and
    // Reactions -- have their own top nav entry; `/vdw-complexes` and
    // `/transition-states` have none, and must not make an unrelated link
    // fire).
    it.each([
        ["/species", "Species"],
        ["/reactions", "Reactions"],
    ])("on %s, the %s nav link (and only that one) is marked aria-current", async (path, activeLabel) => {
        server.use(
            ...emptyVocabHandlers(),
            http.get("/api/v1/scientific/species/browse", () => HttpResponse.json(emptyBrowseEnvelope())),
            http.get("/api/v1/scientific/reactions/browse", () => HttpResponse.json(emptyBrowseEnvelope())),
        )
        window.history.replaceState({}, "", path)
        render(<App />)
        await screen.findByText(/have been deposited in this archive yet/)
        // Scoped to the TOP nav (`aria-label="Primary navigation"`,
        // `AppShell.tsx`) -- on `/reactions`, the browse-kind switcher
        // (`nav[aria-label="Browse a different kind"]`) also renders a
        // "Species" link (species is one of the OTHER three kinds from
        // there), so an unscoped `getByRole("link", { name: "Species" })`
        // would find two and throw.
        const primaryNav = screen.getByRole("navigation", { name: "Primary navigation" })
        expect(within(primaryNav).getByRole("link", { name: activeLabel })).toHaveAttribute("aria-current", "page")
        for (const [label, href] of [["Species", "/species"], ["Reactions", "/reactions"], ["Methods", "/methods"]] as const) {
            if (label === activeLabel) continue
            expect(within(primaryNav).getByRole("link", { name: label })).not.toHaveAttribute("aria-current")
            expect(within(primaryNav).getByRole("link", { name: label })).toHaveAttribute("href", href)
        }
    })

    it.each(["/vdw-complexes", "/transition-states"])("on %s (no dedicated nav link), Species is NOT spuriously marked active", async (path) => {
        server.use(
            ...emptyVocabHandlers(),
            http.get("/api/v1/scientific/species/browse", () => HttpResponse.json(emptyBrowseEnvelope())),
            http.get("/api/v1/scientific/transition-states/browse", () => HttpResponse.json(emptyBrowseEnvelope())),
        )
        window.history.replaceState({}, "", path)
        render(<App />)
        await screen.findByText(/have been deposited in this archive yet/)
        // Scoped to the top nav -- see the aria-current test above for why
        // an unscoped query is ambiguous (the browse-kind switcher also
        // renders a "Species" link on these paths, species being one of
        // the OTHER kinds from vdw/transition_state).
        const primaryNav = screen.getByRole("navigation", { name: "Primary navigation" })
        expect(within(primaryNav).getByRole("link", { name: "Species" })).not.toHaveAttribute("aria-current")
        expect(within(primaryNav).getByRole("link", { name: "Reactions" })).not.toHaveAttribute("aria-current")
    })
})

it("retains the admin machine-review route outside public navigation", async () => {
    window.history.replaceState({}, "", "/admin/machine-review-inspection")
    render(<QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider>)
    expect(await screen.findByRole("heading", { name: "Submission Machine-Review Inspection" })).toBeVisible()
})

it("routes a transition-state-entry ref to its detail page (finding #1)", async () => {
    server.use(http.get("/api/v1/scientific/transition-states/search", () => HttpResponse.json({ records: [] })))
    server.use(http.get("/api/v1/scientific/transition-state-entries/tse_abc", () => HttpResponse.json({
        record: {
            transition_state_entry: {
                transition_state_entry_ref: "tse_abc", charge: 0, multiplicity: 2, status: "optimized",
                unmapped_smiles: "[C]>>C", created_at: "2026-08-05T14:04:16.914780",
                review: { status: "not_reviewed" },
            },
            transition_state: {
                transition_state_ref: "ts_abc", label: "TS0", note: null,
                created_at: "2026-08-05T14:04:16.914780", review: { status: "not_reviewed" },
            },
            reaction: {
                reaction_ref: "rxn_abc", reaction_entry_ref: "rxe_abc",
                equation: "A <=> B", reversible: true, family: "R_Addition_MultipleBond",
            },
            evidence_summary: {
                calculation_count: 0, has_opt: false, has_freq: false, has_sp: false, has_irc: false,
                has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
                levels_of_theory: {},
            },
            validation: { irc: "absent" },
            available_sections: {
                has_entries: true, has_calculations: false, has_geometries: false,
                has_review: false, has_validation_evidence: false,
            },
            calculations: [], geometries: [], review_history: [],
        },
    })))
    window.history.replaceState({}, "", "/transition-state-entries/tse_abc")
    render(<App />)
    expect(await screen.findByRole("heading", { name: "A <=> B" })).toBeVisible()
    expect(window.location.pathname).toBe("/transition-state-entries/tse_abc")
})

it("routes a reaction ref to the chooser page (ReactionOverviewPage)", async () => {
    server.use(http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json({
        review_summary: { total: 1, not_reviewed: 1, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
        records: [{
            reaction_ref: "rxn_abc",
            reaction_entry_ref: "rxe_abc",
            equation: "A <=> B",
            reversible: true,
            family: null,
            review: { status: "not_reviewed" },
            reactants: [{ species_entry_ref: "spe_a", smiles: "A", participant_index: 0 }],
            products: [{ species_entry_ref: "spe_b", smiles: "B", participant_index: 0 }],
            availability: { has_kinetics: false, has_transition_state: false, has_path_search: false, kinetics_count: 0 },
        }],
    })))
    window.history.replaceState({}, "", "/reactions/rxn_abc")
    render(<App />)
    // Waits for a fact that only the LOADED chooser document renders (the
    // loading state's own `<h1>`, "Loading reaction…", would satisfy a
    // bare `findByRole("heading", {level: 1})` immediately and race the
    // real content).
    expect(await screen.findByText("rxn_abc")).toBeVisible()
    // The arrow glyph sits inside a span with its own `aria-label`
    // ("reacts reversibly with") -- that label REPLACES the glyph in the
    // accessible NAME `getByRole`'s `name` option matches against, so this
    // asserts against the h1's visible `textContent` instead.
    const h1 = document.querySelector("h1")
    expect(h1?.textContent).toContain("⇌")
    expect(screen.getByRole("link", { name: "rxe_abc" })).toHaveAttribute("href", "/reaction-entries/rxe_abc")
    // `/reactions` (exact) now renders a real page (BrowsePage) too, not a
    // placeholder -- confirms `/reactions/:reactionRef` still resolves to
    // THIS chooser page rather than being swallowed by the exact-match
    // browse route.
    expect(window.location.pathname).toBe("/reactions/rxn_abc")
})

it("an rxe_ ref handed to /reactions/:ref redirects to /reaction-entries/:ref with no reactions/search request", async () => {
    // No handler is registered for reactions/search at all; `server.listen`
    // is configured `onUnhandledRequest: "error"` at the top of this file,
    // so a fallthrough to the chooser (rather than the prefix-check
    // redirect) would fail this test outright on that request alone.
    server.use(http.get("/api/v1/scientific/reaction-entries/rxe_abc/full", () => HttpResponse.json({
        reaction_entry: {
            reaction_entry_ref: "rxe_abc", reaction_ref: "rxn_abc", equation: "A <=> B",
            reversible: true, family: null, review: { status: "not_reviewed" }, atom_maps: [],
        },
        review_summary: { total: 0, not_reviewed: 0, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
        species: { reactants: [], products: [] },
        kinetics: [], transition_states: [], calculations: [], networks: [],
    })))
    window.history.replaceState({}, "", "/reactions/rxe_abc")
    render(<App />)
    // MEASURED gap this closes (post-review): the OLD `RecordPlaceholderPage`
    // route ALSO rendered the literal text "rxe_abc" (its own `{ref &&
    // <code>{ref}</code>}` line), at the UNCHANGED url `/reactions/rxe_abc`
    // -- so a `findByText("rxe_abc")` assertion alone passed identically
    // whether or not the redirect actually fired, and stayed green when
    // this route was reverted to the placeholder. Asserting the URL itself
    // (App.tsx renders through a real `BrowserRouter`, so `window.location`
    // reflects client-side navigation) is the one signal only the ACTUAL
    // `<Navigate>` redirect produces.
    await screen.findByText("rxe_abc")
    expect(window.location.pathname).toBe("/reaction-entries/rxe_abc")
})

// The owner-reported gap this project exists to close: the home-page search
// only recognised `spc_`/`spe_`; a `rxn_`/`rxe_`/`tse_` reference fell
// through every branch and was sent to the archive as a SMILES structure
// query. These three exercise the SAME public-reference vocabulary the
// tests above already exercise via `window.history.replaceState` (a
// direct link/bookmark), but arrived by TYPING into `IdentifierSearch` on
// the home page instead -- the actual path the owner's report was about.
describe("the home-page identifier search recognizes and routes public references beyond spc_/spe_", () => {
    const REF_BODY = "aaaaaaaaaaaaaaaaaaaaaaaaaa"

    it("a typed rxn_ reference navigates straight to the reaction chooser page", async () => {
        server.use(http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json({
            review_summary: { total: 1, not_reviewed: 1, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
            records: [{
                reaction_ref: `rxn_${REF_BODY}`,
                reaction_entry_ref: `rxe_${REF_BODY}`,
                equation: "A <=> B",
                reversible: true,
                family: null,
                review: { status: "not_reviewed" },
                reactants: [{ species_entry_ref: "spe_a", smiles: "A", participant_index: 0 }],
                products: [{ species_entry_ref: "spe_b", smiles: "B", participant_index: 0 }],
                availability: { has_kinetics: false, has_transition_state: false, has_path_search: false, kinetics_count: 0 },
            }],
        })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), `rxn_${REF_BODY}`)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByText(`rxn_${REF_BODY}`)).toBeVisible()
        expect(window.location.pathname).toBe(`/reactions/rxn_${REF_BODY}`)
    })

    it("a typed rxe_ reference navigates straight to the reaction-entry page, with no /reactions/search request at all", async () => {
        // No handler for `reactions/search` -- this file's server is
        // `onUnhandledRequest: "error"`, so going through the chooser
        // (`/reactions/:ref`, which calls `reactions/search` first) rather
        // than straight to `/reaction-entries/:ref` would fail this test on
        // that request alone.
        server.use(http.get(`/api/v1/scientific/reaction-entries/rxe_${REF_BODY}/full`, () => HttpResponse.json({
            reaction_entry: {
                reaction_entry_ref: `rxe_${REF_BODY}`, reaction_ref: `rxn_${REF_BODY}`, equation: "A <=> B",
                reversible: true, family: null, review: { status: "not_reviewed" }, atom_maps: [],
            },
            review_summary: { total: 0, not_reviewed: 0, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
            species: { reactants: [], products: [] },
            kinetics: [], transition_states: [], calculations: [], networks: [],
        })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), `rxe_${REF_BODY}`)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByText(`rxe_${REF_BODY}`)).toBeVisible()
        expect(window.location.pathname).toBe(`/reaction-entries/rxe_${REF_BODY}`)
    })

    it("a typed tse_ reference navigates straight to the transition-state-entry detail page", async () => {
        server.use(
            http.get("/api/v1/scientific/transition-states/search", () => HttpResponse.json({ records: [] })),
            http.get(`/api/v1/scientific/transition-state-entries/tse_${REF_BODY}`, () => HttpResponse.json({
                record: {
                    transition_state_entry: {
                        transition_state_entry_ref: `tse_${REF_BODY}`, charge: 0, multiplicity: 2, status: "optimized",
                        unmapped_smiles: "[C]>>C", created_at: "2026-08-05T14:04:16.914780",
                        review: { status: "not_reviewed" },
                    },
                    transition_state: {
                        transition_state_ref: "ts_abc", label: "TS0", note: null,
                        created_at: "2026-08-05T14:04:16.914780", review: { status: "not_reviewed" },
                    },
                    reaction: {
                        reaction_ref: `rxn_${REF_BODY}`, reaction_entry_ref: `rxe_${REF_BODY}`,
                        equation: "A <=> B", reversible: true, family: "R_Addition_MultipleBond",
                    },
                    evidence_summary: {
                        calculation_count: 0, has_opt: false, has_freq: false, has_sp: false, has_irc: false,
                        has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
                        levels_of_theory: {},
                    },
                    validation: { irc: "absent" },
                    available_sections: {
                        has_entries: true, has_calculations: false, has_geometries: false,
                        has_review: false, has_validation_evidence: false,
                    },
                    calculations: [], geometries: [], review_history: [],
                },
            })),
        )
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), `tse_${REF_BODY}`)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("heading", { name: "A <=> B" })).toBeVisible()
        expect(window.location.pathname).toBe(`/transition-state-entries/tse_${REF_BODY}`)
    })
})

describe("unmatched routes (finding #12)", () => {
    it("shows the not-found page instead of silently rendering the home page", async () => {
        window.history.replaceState({}, "", "/this-route-does-not-exist")
        render(<App />)
        expect(await screen.findByRole("heading", { name: "No page at this address" })).toBeVisible()
        expect(screen.getByText("/this-route-does-not-exist")).toBeVisible()
        // Not the home page, which this route used to silently redirect to.
        expect(screen.queryByLabelText("Exact species identifier")).not.toBeInTheDocument()
    })

    it("shows the not-found page for a plausible-looking but wrong tab segment, not the first tab", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [overviewSpecies()] })))
        // A guess at a tab route (the real one is `/sp`) used to silently
        // render the Geometry tab -- SpeciesEntryPage's own default -- with
        // no signal the URL was wrong.
        window.history.replaceState({}, "", `/species-entries/${entryRef}/single-point`)
        render(<App />)
        expect(await screen.findByRole("heading", { name: "No page at this address" })).toBeVisible()
        expect(screen.queryByText("Geometry")).not.toBeInTheDocument()
    })

    it("still serves every real species-entry tab segment", async () => {
        server.use(
            http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [overviewSpecies()] })),
            http.get("/api/v1/scientific/conformers/search", () => HttpResponse.json({ records: [] })),
        )
        for (const section of ["geometry", "sp", "statmech", "thermo", "transport"]) {
            window.history.replaceState({}, "", `/species-entries/${entryRef}/${section}`)
            render(<App />)
            expect(await screen.findByRole("heading", { name: "H2O" })).toBeVisible()
            expect(screen.queryByRole("heading", { name: "No page at this address" })).not.toBeInTheDocument()
            expect(window.location.pathname).toBe(`/species-entries/${entryRef}/${section}`)
            cleanup()
        }
    })
})
