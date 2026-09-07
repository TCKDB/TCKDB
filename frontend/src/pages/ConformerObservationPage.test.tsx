import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ConformerObservationPage from "./ConformerObservationPage"

/** Reads the `<dd>` text for a `.kv-list`-shaped `<dt>` term inside `container` --
 *  the same helper `CalculationDetailPage.test.tsx` uses on its own coverage
 *  checklist, reused here now both pages render the identical structure. */
function ddFor(container: HTMLElement, term: string): string {
    const dt = Array.from(container.querySelectorAll("dt")).find((el) => el.textContent === term)
    if (!dt) throw new Error(`No <dt> with text "${term}" found in this container`)
    return dt.nextElementSibling?.textContent ?? ""
}

/** Reads the `.value-pill` span inside the `<dd>` for a `<dt>` term. */
function ddPillFor(container: HTMLElement, term: string): HTMLElement {
    const dt = Array.from(container.querySelectorAll("dt")).find((el) => el.textContent === term)
    if (!dt) throw new Error(`No <dt> with text "${term}" found in this container`)
    const pill = dt.nextElementSibling?.querySelector("span")
    if (!pill) throw new Error(`No pill span found in the <dd> for "${term}"`)
    return pill as HTMLElement
}

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function page() {
    return render(
        <MemoryRouter initialEntries={["/conformer-observations/co_one"]}>
            <Routes>
                <Route path="/conformer-observations/:observationRef" element={<ConformerObservationPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function mockRecord(overrides: Record<string, unknown> = {}) {
    return {
        conformer_observation: {
            conformer_observation_ref: "co_one",
            scientific_origin: "computed",
            note: "Coarse pre-optimisation basin",
            created_at: "2026-07-21T12:06:50.748258",
            review: { status: "reviewed" },
        },
        conformer_group: {
            conformer_group_ref: "cg_demo",
            label: "conformer_1",
            note: null,
            review: { status: "not_reviewed" },
        },
        species: {
            species_ref: "spc_demo",
            species_entry_ref: "spe_demo",
            species_entry_label: "ground state",
            formula: "CH3",
            canonical_smiles: "[CH3]",
        },
        assignment_scheme: null,
        evidence_summary: {
            calculation_count: 3,
            geometry_count: 2,
            has_opt: true,
            has_freq: true,
            has_sp: false,
            has_geometry_validation: true,
            has_scf_stability: false,
            levels_of_theory: {
                opt: [{ level_of_theory_ref: "lot_1", method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }],
                freq: [{ level_of_theory_ref: "lot_2", method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }],
            },
        },
        available_sections: {
            has_observations: true,
            has_selections: false,
            has_calculations: true,
            has_geometries: true,
            has_review: true,
        },
        observations: [
            {
                conformer_observation: {
                    conformer_observation_ref: "co_one",
                    scientific_origin: "computed",
                    note: null,
                    created_at: "2026-07-21T12:06:50.748258",
                    review: { status: "reviewed" },
                },
                conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", review: { status: "not_reviewed" } },
                species: { species_ref: "spc_demo", species_entry_ref: "spe_demo" },
                evidence_summary: {
                    calculation_count: 3, geometry_count: 2, has_opt: true, has_freq: true, has_sp: false,
                    has_geometry_validation: true, has_scf_stability: false, levels_of_theory: {},
                },
                available_sections: {
                    has_observations: true, has_selections: false, has_calculations: true, has_geometries: true, has_review: true,
                },
            },
            {
                conformer_observation: {
                    conformer_observation_ref: "co_two",
                    scientific_origin: "computed",
                    note: null,
                    created_at: "2026-07-21T12:14:32.845900",
                    review: { status: "not_reviewed" },
                },
                conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", review: { status: "not_reviewed" } },
                species: { species_ref: "spc_demo", species_entry_ref: "spe_demo" },
                evidence_summary: {
                    calculation_count: 1, geometry_count: 1, has_opt: true, has_freq: false, has_sp: false,
                    has_geometry_validation: false, has_scf_stability: false, levels_of_theory: {},
                },
                available_sections: {
                    has_observations: true, has_selections: false, has_calculations: true, has_geometries: true, has_review: true,
                },
            },
        ],
        calculations: [
            {
                calculation_ref: "calc_opt",
                type: "opt",
                quality: "raw",
                review: { status: "reviewed" },
                level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
                software_release: { software: "Gaussian" },
                workflow_tool_release: { workflow_tool: "ARC" },
            },
            {
                calculation_ref: "calc_freq",
                type: "freq",
                quality: "raw",
                review: { status: "not_reviewed" },
                level_of_theory: { method: "wb97xd", basis: "def2tzvp" },
                software_release: { software: "Gaussian" },
            },
        ],
        geometries: [
            { calculation_ref: "calc_opt", geometry: { geometry_ref: "geo_one", natoms: 4 } },
            { calculation_ref: "calc_freq", geometry: { geometry_ref: "geo_two", natoms: 4 } },
        ],
        review_history: [
            { status: "reviewed", reviewed_at: "2026-07-25T00:00:00", note: "Looks consistent" },
        ],
        selections: [],
        ...overrides,
    }
}

describe("ConformerObservationPage", () => {
    it("keeps observation, calculation-row and geometry counts in their own metric", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include")).toEqual([
                "observations", "selections", "calculations", "geometries", "review",
            ])
            return HttpResponse.json({ record: mockRecord() })
        }))

        page()
        expect(await screen.findByRole("heading", { name: "Computed observation" })).toBeVisible()

        // Each count lives in its own metric card — scoping the query to the
        // card catches a swap between "Calculation rows" and "Distinct
        // stored geometries" that a page-wide getByText("3") would miss.
        const calcMetric = screen.getByText("Calculation rows").closest(".metric")
        const geomMetric = screen.getByText("Distinct stored geometries").closest(".metric")
        const siblingMetric = screen.getByText("Other observations in this basin").closest(".metric")
        expect(calcMetric).not.toBeNull()
        expect(geomMetric).not.toBeNull()
        expect(siblingMetric).not.toBeNull()
        expect(within(calcMetric as HTMLElement).getByText("3")).toBeVisible()
        expect(within(geomMetric as HTMLElement).getByText("2")).toBeVisible()
        expect(within(siblingMetric as HTMLElement).getByText("1")).toBeVisible()
    })

    // Item 1/6/7, design/foundations PR B: mutation check for header order,
    // the review pill's `.value-pill` primitive, and the calculation
    // table's `.data-table` primitive -- see the identical check on
    // `ConformerGroupPage.test.tsx`.
    it("renders the kicker row before the h1, the review pill as .value-pill, and the calculation table as .data-table", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        const h1 = await screen.findByRole("heading", { name: "Computed observation" })
        // SHOULD-FIX-7 (PR B review): same `.record-identity-header`
        // wrapper `RecordIdentityHeader` itself renders -- see the
        // identical check/comment on `ConformerGroupPage.test.tsx`.
        const wrapper = h1.closest(".record-identity-header") as HTMLElement
        expect(wrapper).not.toBeNull()
        const kickerRow = wrapper.querySelector(".record-identity-kicker-row") as HTMLElement
        expect(kickerRow).not.toBeNull()
        const order = Array.from(wrapper.children)
        expect(order.indexOf(kickerRow)).toBeLessThan(order.indexOf(h1))

        const pill = within(kickerRow).getByText("reviewed")
        expect(pill).toHaveClass("value-pill")
        expect(pill).not.toHaveClass("value-pill--muted")
        expect(document.querySelector(".review-badge")).toBeNull()

        expect(document.querySelector(".stage-table")).toBeNull()
        const table = screen.getByRole("table", { name: "Calculations for co_one" })
        expect(table).toHaveClass("data-table")
        expect(table.closest(".table-scroll")).not.toBeNull()
    })

    it("states each stage's level of theory once, in the calculation table, not in a separate by-stage block", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))

        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        // The standalone "Levels of theory by stage" section is gone -- it
        // duplicated the Stage/Level-of-theory columns of the calculation
        // table immediately below it.
        expect(screen.queryByRole("heading", { name: "Levels of theory by stage" })).not.toBeInTheDocument()

        // The calculation table is still the one place carrying this: each
        // row pairs its own stage with its own level of theory.
        const calcTable = screen.getByRole("table", { name: "Calculations for co_one" })
        const optRow = within(calcTable).getByText("opt").closest("tr")
        const freqRow = within(calcTable).getByText("freq").closest("tr")
        expect(optRow).not.toBeNull()
        expect(freqRow).not.toBeNull()
        expect(within(optRow as HTMLElement).getByText("b3lyp/def2tzvp")).toBeVisible()
        expect(within(freqRow as HTMLElement).getByText("wb97xd/def2tzvp")).toBeVisible()
    })

    it("links breadcrumbs and provenance rows, and surfaces stable public refs", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))

        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(breadcrumb).getByRole("link", { name: "Species" }))
            .toHaveAttribute("href", "/species/spc_demo")
        expect(within(breadcrumb).getByRole("link", { name: "Species entry" }))
            .toHaveAttribute("href", "/species-entries/spe_demo")
        expect(within(breadcrumb).getByRole("link", { name: "Conformer basin" }))
            .toHaveAttribute("href", "/conformer-groups/cg_demo")

        // Item 4/5 ("record-page residuals" re-review): the species-entry
        // body link no longer shows the deposited label ("ground state" in
        // this fixture) ALONE. This endpoint's `species` context now
        // carries `formula` (backend fix), so `SpeciesEntryLink`'s base
        // text is the formula -- with the label (unrecognised by
        // `stereoChip`, so rendered unchanged) riding along after it --
        // see `SpeciesEntryLink.test.tsx` for the component-level version
        // of this assertion, and that component's own docstring for why
        // `species_entry_label` is a computed discriminator, not free
        // text, and is not suppressed. A sibling test below covers the
        // no-`formula` case, where the base text falls back to the entry
        // REF (`<code className="data">`) instead -- never the literal
        // words "Species entry" (the enclosing <dt> already says that).
        const identityHeader = document.querySelector(".record-identity-header") as HTMLElement
        const speciesEntryLink = within(identityHeader).getByRole("link", { name: "CH3 · ground state" })
        expect(speciesEntryLink).toHaveAttribute("href", "/species-entries/spe_demo")

        // The conformer-basin label ("conformer_1") is a DIFFERENT fact
        // (a depositor label on the conformer group, not the species
        // entry) and is out of scope for this fix -- see the PR body's
        // "Other depositor strings still rendered" list.
        expect(screen.getByRole("link", { name: "conformer_1" }))
            .toHaveAttribute("href", "/conformer-groups/cg_demo")

        // Stable public refs stay visible and copyable even when a label exists.
        expect(screen.getByText("co_one", { selector: "code" })).toBeVisible()
        expect(screen.getByText("cg_demo", { selector: "code" })).toBeVisible()
        expect(screen.getByText("spc_demo", { selector: "code" })).toBeVisible()

        // Sibling list excludes this observation itself and links onward.
        expect(screen.getByRole("link", { name: "co_two" })).toHaveAttribute(
            "href", "/conformer-observations/co_two",
        )
        expect(screen.queryByRole("link", { name: "co_one" })).not.toBeInTheDocument()

        // Review-trust layer is separate from the summary metrics.
        expect(screen.getByRole("heading", { name: "Review history" })).toBeVisible()
        expect(screen.getByText("Looks consistent")).toBeVisible()

        // Geometry links point at the geometry detail route.
        expect(screen.getByRole("link", { name: "geo_one" })).toHaveAttribute("href", "/geometries/geo_one")
    })

    // Owner decision: "yes show each record's own ref inline" -- this
    // page already showed "Observation ref" first, but with no copy
    // button; every other record page's own-ref fact gets one via
    // `RecordIdentityHeader`'s `ownRef` prop, so this hand-rolled page
    // now matches that affordance.
    it("shows the observation's own ref first in the identity block, with the data face and a copy button", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))

        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        const identityFacts = document.querySelector(".record-identity-header dl.kv-list") as HTMLElement
        expect(identityFacts).not.toBeNull()
        expect(Array.from(identityFacts.children)[0]).toHaveTextContent("Observation ref")
        const ownRefValue = within(identityFacts).getByText("co_one", { selector: "code" })
        expect(ownRefValue).toBeVisible()
        expect(ownRefValue).toHaveClass("data")
        expect(screen.getByRole("button", { name: /copy observation ref/i })).toBeVisible()
    })

    // Unified fallback rule (per `SpeciesEntryLink`'s own reviewer-flagged
    // duplication fix, shared with `ConformerGroupPage.tsx` and
    // `RecordIdentityHeader.tsx`): when the species SMILES did not parse
    // and the backend serves no `formula`, the base link text is the
    // entry REF as `<code className="data">`, never the literal words
    // "Species entry" -- the enclosing <dt> already says that.
    it("falls back to the entry ref, as a data code run, when the species context carries no formula", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    species: {
                        species_ref: "spc_demo",
                        species_entry_ref: "spe_demo",
                        species_entry_label: "ground state",
                        formula: null,
                        canonical_smiles: "not-a-smiles(((",
                    },
                }),
            })
        )))

        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        const identityHeader = document.querySelector(".record-identity-header") as HTMLElement
        const speciesEntryLink = within(identityHeader).getByRole("link", { name: "spe_demo · ground state" })
        expect(speciesEntryLink).toHaveAttribute("href", "/species-entries/spe_demo")
        expect(within(speciesEntryLink).getByText("spe_demo")).toHaveClass("data")
        expect(speciesEntryLink.textContent).not.toContain("Species entry")
    })

    // Same shape as the fix on `CalculationDetailPage.tsx`'s `OwnerCard`:
    // a separate "Group ref" row is only needed when the "Conformer basin"
    // link above shows a LABEL, not the ref itself. Without a label, the
    // link already shows the ref, and a second row would repeat it.
    it("omits the duplicate 'Group ref' row when the basin has no label (the link already shows the ref)", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    conformer_group: { conformer_group_ref: "cg_demo", label: null, note: null, review: { status: "not_reviewed" } },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        expect(screen.getByRole("link", { name: "cg_demo" })).toHaveAttribute("href", "/conformer-groups/cg_demo")
        expect(screen.getAllByText("cg_demo")).toHaveLength(1)
        expect(screen.queryByText("Group ref")).not.toBeInTheDocument()
    })

    it("collapses review history to one line when its only entry carries no real event", async () => {
        // A `review_history` row whose `reviewed_at` and `note` are both
        // null mirrors the observation's current status without recording
        // an actual status-change event -- the owner's report: a one-row
        // table reading "not reviewed / not recorded / not recorded" that
        // said nothing the hero badge hadn't already said.
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    conformer_observation: {
                        conformer_observation_ref: "co_one",
                        scientific_origin: "computed",
                        note: "Coarse pre-optimisation basin",
                        created_at: "2026-07-21T12:06:50.748258",
                        review: { status: "not_reviewed" },
                    },
                    review_history: [{ status: "not_reviewed", reviewed_at: null, note: null }],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        expect(screen.getByRole("heading", { name: "Review history" })).toBeVisible()
        expect(screen.queryByRole("table", { name: /Review history for/ })).not.toBeInTheDocument()
        expect(screen.getByText("No review events are recorded for this observation.")).toBeVisible()
        // The intro sentence no longer restates the raw status value --
        // that already lives once, in the hero badge.
        expect(screen.queryByText(/The current status is/)).not.toBeInTheDocument()
    })

    it("still renders the review-history table when a real event is recorded", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        expect(screen.getByRole("table", { name: "Review history for co_one" })).toBeVisible()
        expect(screen.getByText("Looks consistent")).toBeVisible()
    })

    it("reports a check as 'recorded', never as a pass/fail verdict", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        // record-summary-row PR: the inline run-on sentence ("opt yes ·
        // freq yes · ...") is gone -- each check is now its own labelled
        // row in the evidence checklist (`.coverage-checklist`, the SAME
        // shape `CalculationDetailPage`'s own checklist uses).
        const checklist = document.querySelector(".coverage-checklist") as HTMLElement
        expect(checklist).not.toBeNull()
        expect(ddFor(checklist, "Geometry validation")).toBe("recorded")
        expect(ddFor(checklist, "SCF stability")).toBe("not recorded")
    })

    // record-summary-row PR, item 1: the evidence box sits BELOW the tile
    // row now, as its own full-width `.ledger-summary--single` section --
    // never a 4th item sharing the tiles' own grid row (the owner report
    // this fixes). And item 3: every check is present/absent (or
    // recorded/not recorded) as its own pilled row, using the SAME
    // `value-pill`/`value-pill--muted` pair every other bounded-status-word
    // checklist on this app uses.
    it("renders the tile row and the evidence checklist as two separate sections, tiles with no evidence card among them", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        const tileRow = screen.getByLabelText("Observation evidence summary")
        expect(tileRow).not.toHaveClass("ledger-summary--single")
        expect(tileRow.querySelector(".coverage-card")).toBeNull()
        expect(within(tileRow).getAllByText(/Calculation rows|Distinct stored geometries|Other observations in this basin/)).toHaveLength(3)

        const checklistSection = screen.getByLabelText("Observation evidence checklist")
        expect(checklistSection).toHaveClass("ledger-summary", "ledger-summary--single")
        const card = checklistSection.querySelector(".card.card--derived.coverage-card") as HTMLElement
        expect(card).not.toBeNull()
        expect(within(card).getByText("Evidence on this observation")).toHaveClass("t-label")

        const checklist = card.querySelector(".coverage-checklist") as HTMLElement
        // Default mockRecord: has_opt/has_freq true (present), has_sp false
        // (absent), has_geometry_validation true (recorded),
        // has_scf_stability false (not recorded) -- see the mockRecord
        // definition above.
        for (const [label, value] of [
            ["Optimisation", "present"], ["Frequency", "present"], ["Geometry validation", "recorded"],
        ] as const) {
            const pill = ddPillFor(checklist, label)
            expect(pill).toHaveClass("value-pill")
            expect(pill).not.toHaveClass("value-pill--muted")
            expect(pill).toHaveTextContent(value)
        }
        for (const [label, value] of [["Single point", "absent"], ["SCF stability", "not recorded"]] as const) {
            const pill = ddPillFor(checklist, label)
            expect(pill).toHaveClass("value-pill", "value-pill--muted")
            expect(pill).toHaveTextContent(value)
        }
    })

    // Post-review (review of 2bd17511): the tile-alignment fix moved from a
    // reserved label height to number-first markup -- the number (`<strong>`)
    // must be each tile's FIRST child, with the label (`<span>`) after it,
    // so the number always sits at the tile's own top edge regardless of
    // whether the label wraps.
    it("renders each metric tile number-first: <strong> before <span>", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        const tiles = screen.getByLabelText("Observation evidence summary").querySelectorAll(".metric")
        expect(tiles).toHaveLength(3)
        for (const tile of tiles) {
            expect(tile.children[0].tagName).toBe("STRONG")
            expect(tile.children[1].tagName).toBe("SPAN")
        }
    })

    it("gives the disclosure a real heading so heading-navigation does not skip it", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord({ selections: [{ selection_kind: "lowest_energy" }] }) })
        )))
        page()
        const heading = await screen.findByRole("heading", { name: "Curation selections (1)" })
        expect(heading).toBeVisible()
        // BLOCKING-3 fix (PR B review): the heading sits OUTSIDE the
        // `Disclosure` that collapses the selections list -- never nested
        // inside its `<summary>` (a 28px serif h2 never belonged inside a
        // 13px summary row).
        expect(heading.closest("summary")).toBeNull()
    })

    // BLOCKING-3 mutation check: no heading element sits inside any
    // `<summary>` on this page. Put an h2 back inside a `Disclosure`'s
    // `summary` prop and this test fails.
    it("never puts a heading (h1-h4) inside a <summary> on this page", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord({ selections: [{ selection_kind: "lowest_energy" }] }) })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        const summaries = document.querySelectorAll("summary")
        expect(summaries.length).toBeGreaterThan(0)
        for (const summary of summaries) {
            expect(summary.querySelector("h1, h2, h3, h4")).toBeNull()
        }
    })

    it("shows a specific not-found state for a 404", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => {
            return HttpResponse.json({}, { status: 404 })
        }))
        page()
        expect(await screen.findByRole("heading", { name: "Conformer observation not found" })).toBeVisible()
    })

    it("gives a wrong-handle-type 422 its own non-retryable state, distinct from a transient outage", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => HttpResponse.json({
            code: "handle_type_mismatch",
            detail: "handle_type_mismatch: expected a conformer_observation handle (prefix 'co') but got prefix 'cg'",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a conformer observation reference" })).toBeVisible()
        expect(screen.getByText(/expected a conformer_observation handle/)).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Conformer observation unavailable" })).not.toBeInTheDocument()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    it("gives a malformed-ref 422 (code invalid_handle) its own non-retryable state, distinct from a wrong-prefix ref", async () => {
        // `invalid_handle` — right prefix, unparseable body — is distinct
        // from `handle_type_mismatch` above and is what live traffic
        // actually returns for a malformed ref. This pins the shared
        // `INVALID_HANDLE_CODES` classification in `useScientificRecord`
        // on this page too, not just the surface it was changed for.
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => HttpResponse.json({
            code: "invalid_handle",
            detail: "invalid_handle: 'co_' not a recognised conformer_observation handle",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a conformer observation reference" })).toBeVisible()
        expect(screen.getByText(/not a recognised conformer_observation handle/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    it("treats an absent calculations key as 'not requested', distinct from an empty one", async () => {
        const withoutKey = mockRecord()
        delete (withoutKey as Record<string, unknown>).calculations
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: withoutKey })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        expect(screen.getByText("This section was not requested for this view.")).toBeVisible()
        expect(screen.queryByText("No calculation rows were returned for this observation.")).not.toBeInTheDocument()
    })

    it("renders calculations: null the same as calculations: [] — both are 'requested, empty'", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: null,
                    available_sections: {
                        has_observations: true, has_selections: false,
                        has_calculations: false, has_geometries: true, has_review: true,
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        expect(screen.getByText("No calculation rows were returned for this observation.")).toBeVisible()
    })

    it("flags a contradiction when the archive marks evidence present but returns none", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: [],
                    available_sections: {
                        has_observations: true, has_selections: false,
                        has_calculations: true, has_geometries: true, has_review: true,
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        expect(screen.getByText(/The archive marks this observation as having recorded evidence here/)).toBeVisible()
    })

    it("renders a single-observation basin with no sibling list, not an error", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    observations: [{
                        conformer_observation: {
                            conformer_observation_ref: "co_one",
                            scientific_origin: "computed",
                            note: null,
                            created_at: "2026-07-21T12:06:50.748258",
                            review: { status: "reviewed" },
                        },
                        conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", review: { status: "not_reviewed" } },
                        species: { species_ref: "spc_demo", species_entry_ref: "spe_demo" },
                        evidence_summary: {
                            calculation_count: 3, geometry_count: 2, has_opt: true, has_freq: true, has_sp: false,
                            has_geometry_validation: true, has_scf_stability: false, levels_of_theory: {},
                        },
                        available_sections: {
                            has_observations: true, has_selections: false, has_calculations: true,
                            has_geometries: true, has_review: true,
                        },
                    }],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        const siblingMetric = screen.getByText("Other observations in this basin").closest(".metric")
        expect(within(siblingMetric as HTMLElement).getByText("0")).toBeVisible()
        expect(screen.getByText("No other deposited observations were returned for this basin.")).toBeVisible()
        expect(screen.queryByRole("link", { name: "co_two" })).not.toBeInTheDocument()
    })

    // Reproduces the owner's exact report: the same "not reviewed" status
    // showing up eight times on one record (hero pill, a Review column on
    // every calculation row, and a pill on every sibling). Review status is
    // now shown once, in the hero -- the calculation table drops its Review
    // column entirely (calculations don't carry independent review states
    // on this surface; their evidence is the observation's own), and a
    // sibling only gets its own pill when its status genuinely differs from
    // this record's.
    it("shows review status once, in the hero, not per calculation row or per matching sibling", async () => {
        const siblingObservation = (ref: string, createdAt: string) => ({
            conformer_observation: {
                conformer_observation_ref: ref,
                scientific_origin: "computed",
                note: null,
                created_at: createdAt,
                review: { status: "not_reviewed" },
            },
            conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", review: { status: "not_reviewed" } },
            species: { species_ref: "spc_demo", species_entry_ref: "spe_demo" },
            evidence_summary: {
                calculation_count: 1, geometry_count: 1, has_opt: true, has_freq: false, has_sp: false,
                has_geometry_validation: false, has_scf_stability: false, levels_of_theory: {},
            },
            available_sections: {
                has_observations: true, has_selections: false, has_calculations: true, has_geometries: true, has_review: true,
            },
        })

        const record = mockRecord({
            conformer_observation: {
                conformer_observation_ref: "co_one",
                scientific_origin: "computed",
                note: null,
                created_at: "2026-07-21T12:06:50.748258",
                review: { status: "not_reviewed" },
            },
            observations: [
                siblingObservation("co_one", "2026-07-21T12:06:50.748258"),
                siblingObservation("co_two", "2026-07-21T12:14:32.845900"),
                siblingObservation("co_three", "2026-07-21T12:15:00.000000"),
                siblingObservation("co_four", "2026-07-21T12:16:00.000000"),
            ],
            calculations: [
                { calculation_ref: "calc_opt", type: "opt", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software_release: { software: "Gaussian" } },
                { calculation_ref: "calc_freq", type: "freq", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: { method: "wb97xd", basis: "def2tzvp" }, software_release: { software: "Gaussian" } },
                { calculation_ref: "calc_sp", type: "sp", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: { method: "ccsd(t)", basis: "cbs" }, software_release: { software: "Molpro" } },
                { calculation_ref: "calc_opt2", type: "opt", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software_release: { software: "Gaussian" } },
            ],
        })

        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record })
        )))

        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        // Positive assertion: the calculation table still renders all four
        // rows (this is not an absence check -- the rows are there, just
        // without their own Review column).
        const calcTable = screen.getByRole("table", { name: "Calculations for co_one" })
        expect(within(calcTable).getAllByRole("row")).toHaveLength(5) // header + 4 calculation rows
        expect(within(calcTable).queryByRole("columnheader", { name: "Review" })).not.toBeInTheDocument()

        // Exactly one "not reviewed" on the whole page -- the hero pill.
        // The three siblings all share this observation's own status, so
        // none of them gets its own pill.
        expect(screen.getAllByText("not reviewed")).toHaveLength(1)
    })

    it("gives a sibling its own review pill only when its status differs from this observation's", async () => {
        // This observation is "not_reviewed"; co_two is deliberately
        // flipped to "reviewed" so it genuinely differs and must show its
        // own pill.
        const record = mockRecord({
            conformer_observation: {
                conformer_observation_ref: "co_one",
                scientific_origin: "computed",
                note: null,
                created_at: "2026-07-21T12:06:50.748258",
                review: { status: "not_reviewed" },
            },
            observations: [
                {
                    conformer_observation: {
                        conformer_observation_ref: "co_one",
                        scientific_origin: "computed",
                        note: null,
                        created_at: "2026-07-21T12:06:50.748258",
                        review: { status: "not_reviewed" },
                    },
                    conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", review: { status: "not_reviewed" } },
                    species: { species_ref: "spc_demo", species_entry_ref: "spe_demo" },
                    evidence_summary: {
                        calculation_count: 3, geometry_count: 2, has_opt: true, has_freq: true, has_sp: false,
                        has_geometry_validation: true, has_scf_stability: false, levels_of_theory: {},
                    },
                    available_sections: {
                        has_observations: true, has_selections: false, has_calculations: true, has_geometries: true, has_review: true,
                    },
                },
                {
                    conformer_observation: {
                        conformer_observation_ref: "co_two",
                        scientific_origin: "computed",
                        note: null,
                        created_at: "2026-07-21T12:14:32.845900",
                        review: { status: "reviewed" },
                    },
                    conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", review: { status: "not_reviewed" } },
                    species: { species_ref: "spc_demo", species_entry_ref: "spe_demo" },
                    evidence_summary: {
                        calculation_count: 1, geometry_count: 1, has_opt: true, has_freq: false, has_sp: false,
                        has_geometry_validation: false, has_scf_stability: false, levels_of_theory: {},
                    },
                    available_sections: {
                        has_observations: true, has_selections: false, has_calculations: true, has_geometries: true, has_review: true,
                    },
                },
            ],
        })

        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record })
        )))

        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        const siblingRow = screen.getByRole("link", { name: "co_two" }).closest("li")
        expect(siblingRow).not.toBeNull()
        expect(within(siblingRow as HTMLElement).getByText("reviewed")).toBeVisible()
    })
})
