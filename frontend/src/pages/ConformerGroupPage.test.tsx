import { StrictMode } from "react"
import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ConformerGroupPage from "./ConformerGroupPage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
    vi.useRealTimers()
})
afterAll(() => server.close())

function page() {
    return render(
        <MemoryRouter initialEntries={["/conformer-groups/cg_demo"]}>
            <Routes>
                <Route path="/conformer-groups/:groupRef" element={<ConformerGroupPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

// `main.tsx` wraps the whole app in `<React.StrictMode>`, which in dev
// double-invokes every effect: mount -> run cleanup -> mount again, all
// synchronously, before the real (surviving) mount's effect has any
// chance to observe a response. `useScientificRecord`/`useSpeciesEntry`
// route their fetch through `api/requestCache.ts`'s `dedupedFetch`, which
// used to run the FIRST mount's own `AbortSignal` into the shared
// request -- so the discarded probe mount's cleanup aborted the one real
// request before the surviving mount ever got a response of its own. See
// `requestCache.ts`'s module docstring for the fix (subscriber counting
// with a deferred abort).
function strictPage() {
    return render(
        <StrictMode>
            <MemoryRouter initialEntries={["/conformer-groups/cg_demo"]}>
                <Routes>
                    <Route path="/conformer-groups/:groupRef" element={<ConformerGroupPage />} />
                </Routes>
            </MemoryRouter>
        </StrictMode>,
    )
}

const payload = {
    record: {
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
        observations_summary: { total: 2, by_scientific_origin: { computed: 2 } },
        evidence_summary: {
            calculation_count: 3,
            optimization_chain_count: 1,
            geometry_count: 2,
            evidence_coverage: { opt: 2, freq: 1, sp: 1 },
        },
        observations: [{
            conformer_observation: {
                conformer_observation_ref: "co_one",
                scientific_origin: "computed",
                review: { status: "reviewed" },
            },
            evidence_summary: {
                calculation_count: 2,
                geometry_count: 1,
                has_opt: true,
                has_freq: true,
                has_sp: false,
                levels_of_theory: {},
            },
            calculations: [
                {
                    calculation_ref: "calc_opt",
                    type: "optimization",
                    quality: "ok",
                    review: { status: "reviewed" },
                    level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
                    software_release: { software: "Gaussian" },
                    workflow_tool_release: { workflow_tool: "ARC" },
                },
                {
                    calculation_ref: "calc_freq",
                    type: "frequency",
                    quality: "ok",
                    review: { status: "not_reviewed" },
                    level_of_theory: { method: "wb97xd", basis: "def2tzvp" },
                    software_release: { software: "Gaussian" },
                },
            ],
            geometries: [{
                calculation_ref: "calc_opt",
                geometry: { geometry_ref: "geo_one", natoms: 4 },
            }],
        }],
        calculations: [],
        geometries: [
            { calculation_ref: "calc_opt", geometry: { geometry_ref: "geo_one", natoms: 4 } },
            { calculation_ref: "calc_sp", geometry: { geometry_ref: "geo_one", natoms: 4 } },
            { calculation_ref: "calc_freq", geometry: { geometry_ref: "geo_two", natoms: 4 } },
        ],
    },
}

describe("ConformerGroupPage", () => {
    it("keeps observations, calculation stages, methods, and geometry inventory distinct", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include"))
                .toEqual(["observations", "calculations", "geometries"])
            return HttpResponse.json(payload)
        }))

        page()
        expect(await screen.findByRole("heading", { name: "Conformer basin" })).toBeVisible()
        expect(screen.getByText(/One torsional basin, shown through its deposited observations/))
            .toBeVisible()
        // selector: "strong" -- the metric tile's own element (`Metric`
        // renders its number in a `<strong>`) -- disambiguates from the
        // "3" subscript inside this fixture's own "CH3" formula rendering
        // (`Formula` renders the digit as a `<sub>`), now that the
        // species-entry link shows the served formula.
        expect(screen.getByText("3", { selector: "strong" })).toBeVisible()
        expect(screen.getByText("1 optimisation chains")).toBeVisible()

        // The observation-scoped evidence ledger is open by default on this
        // page -- its content is asserted directly, no click needed.
        expect(screen.getByText("b3lyp/def2tzvp")).toBeVisible()
        expect(screen.getByText("wb97xd/def2tzvp")).toBeVisible()
        expect(screen.getByText("reviewed", { selector: "td" })).toBeVisible()
        expect(screen.getByText(/produced by calc_opt, calc_sp/)).toBeVisible()
        expect(screen.getByText((_, element) => (
            element?.textContent === "geo_one from calc_opt"
        ))).toBeVisible()
        expect(screen.getAllByRole("link", { name: "geo_one" })[0]).toHaveAttribute(
            "href",
            "/geometries/geo_one",
        )
        expect(screen.getByText(/Their count is not a conformer count/)).toBeVisible()
    })

    // record-summary-row PR, item 1: the coverage card sits BELOW the tile
    // row now, as its own full-width `.ledger-summary--single` section --
    // never a 4th item sharing the tiles' own grid row. Item 3: each stage's
    // coverage is its own row ("N of M observations"), a count, so no
    // `.value-pill` -- unlike the observation page's present/absent checks,
    // this is not a bounded-vocabulary status word (see `EvidenceChecklist`'s
    // own docstring for that distinction).
    it("renders the tile row and the coverage checklist as two separate sections, one row per stage with plain-text counts", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })

        const tileRow = screen.getByLabelText("Basin evidence summary")
        expect(tileRow).not.toHaveClass("ledger-summary--single")
        expect(tileRow.querySelector(".coverage-card")).toBeNull()

        const checklistSection = screen.getByLabelText("Basin evidence checklist")
        expect(checklistSection).toHaveClass("ledger-summary", "ledger-summary--single")
        const card = checklistSection.querySelector(".card.card--derived.coverage-card") as HTMLElement
        expect(card).not.toBeNull()
        expect(within(card).getByText("Evidence on this conformer group")).toHaveClass("t-label")

        const checklist = card.querySelector(".coverage-checklist") as HTMLElement
        for (const [label, value] of [
            ["Optimisation", "2 of 2 observations"],
            ["Frequency", "1 of 2 observations"],
            ["Single point", "1 of 2 observations"],
        ] as const) {
            const dt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === label)
            const dd = dt?.nextElementSibling as HTMLElement
            expect(dd).toHaveTextContent(value)
            // A count, not a status word -- plain text, no pill wrapper.
            expect(dd.querySelector(".value-pill")).toBeNull()
        }
    })

    // Independent review: `toHaveTextContent` above is true whether or not
    // the card is open -- `EvidenceChecklist` collapses this card by
    // default (item 2), so confirm with a real visibility check that
    // opening it (the only way a reader reaches these rows) surfaces the
    // same content. Also pins the TRUE roll-up (`stageCoverageSummary`,
    // `ConformerGroupPage.tsx`) this fixture computes: opt=2/2 (covered),
    // freq=1/2, sp=1/2 (both NOT covered) -> "1 of 3 stages covered", not
    // a bare row count ("3 rows" reads identically for 1-of-3 and 3-of-3).
    it("the checklist card is collapsed behind a TRUE stage-coverage roll-up, and opening it makes the rows actually visible", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })

        const card = screen.getByLabelText("Basin evidence checklist").querySelector(".card.card--derived.coverage-card") as HTMLElement
        const rollup = card.querySelector(".coverage-checklist-summary") as HTMLElement
        expect(rollup).toHaveTextContent("1 of 3 stages covered")

        const details = card.querySelector("details") as HTMLDetailsElement
        const checklist = card.querySelector(".coverage-checklist") as HTMLElement
        expect(details.open).toBe(false)
        expect(checklist).not.toBeVisible()

        fireEvent.click(details.querySelector("summary")!)
        expect(details.open).toBe(true)
        expect(checklist).toBeVisible()
        const optDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Optimisation")
        const optDd = optDt?.nextElementSibling as HTMLElement
        expect(optDd).toBeVisible()
        expect(optDd.textContent).toBe("2 of 2 observations")
    })

    // Post-review (review of 2bd17511): number-first tile markup -- and
    // specifically, THIS page's "Calculation rows" tile is the one caller
    // with a `<small>` detail line ("N optimisation chains"). The detail
    // must sit AFTER the label, never between the number and its own
    // label, so it cannot displace the number the way a wrapping LABEL
    // used to.
    it("renders each metric tile number-first, with the detail line (where present) last, never before the label", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        const tiles = screen.getByLabelText("Basin evidence summary").querySelectorAll(".metric")
        expect(tiles).toHaveLength(3)
        for (const tile of tiles) {
            expect(tile.children[0].tagName).toBe("STRONG")
            expect(tile.children[1].tagName).toBe("SPAN")
        }
        const calcRowsTile = within(screen.getByLabelText("Basin evidence summary")).getByText("Calculation rows").closest(".metric") as HTMLElement
        expect(calcRowsTile.children).toHaveLength(3)
        expect(calcRowsTile.children[0]).toHaveTextContent("3")
        expect(calcRowsTile.children[0].tagName).toBe("STRONG")
        expect(calcRowsTile.children[1].tagName).toBe("SPAN")
        expect(calcRowsTile.children[2].tagName).toBe("SMALL")
        expect(calcRowsTile.children[2]).toHaveTextContent("1 optimisation chains")
    })

    // Design/foundations PR B (item 5, BLOCKING-3 fix per review): this
    // disclosure now composes the shared `Disclosure` primitive
    // (`.disclosure`, `design-system.css`) instead of a page-local
    // `<details className="ledger-section">` recipe -- and the section
    // heading is a plain, always-visible `SectionHeading` OUTSIDE the
    // disclosure, never an h2 nested inside its `<summary>` (a 28px serif
    // heading never belonged inside a 13px summary row). `Disclosure` is
    // deliberately UNCONTROLLED -- it does not set `aria-expanded` itself
    // (native `<details>`/`<summary>` already conveys expanded state to
    // real assistive tech on its own) -- so this test asserts the native
    // `open` attribute and content visibility directly, the same
    // mechanism `CalculationDetailPage`'s `LazySection` tests already
    // rely on for the identical primitive.
    it("opens the observation-scoped evidence ledger by default, naming its count in the disclosure summary, and keeps its heading outside the disclosure", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })

        const heading = screen.getByRole("heading", { name: "Observation-scoped evidence" })
        // BLOCKING-3: the heading itself is never inside a details/summary.
        expect(heading.closest("details")).toBeNull()
        expect(heading.closest("summary")).toBeNull()

        const summary = screen.getByText("1 deposited observation").closest("summary") as HTMLElement
        const details = summary?.closest("details") as HTMLElement
        expect(details).not.toBeNull()
        // Mutation check: rename the class away and this fails.
        expect(details).toHaveClass("disclosure")

        // Open by default: this is the only deposited-evidence section this
        // record page has, so a reader lands with it already expanded.
        expect(heading).toBeVisible()
        expect(details).toHaveAttribute("open")
        expect(screen.getByText("b3lyp/def2tzvp")).toBeVisible()

        // <summary> is still a real, native, keyboard-operable disclosure
        // control -- a reader who wants it collapsed can still close it.
        // The native `open` DOM attribute flips synchronously on click, and
        // jsdom implements native `<details>` toggle behaviour (the same
        // mechanism `LazySection`'s own tests rely on), so no `waitFor` is
        // needed for either assertion below.
        fireEvent.click(summary)

        expect(details).not.toHaveAttribute("open")
        expect(screen.getByText("b3lyp/def2tzvp")).not.toBeVisible()
        // The heading itself is unaffected by the toggle -- it was never
        // inside the collapsing element to begin with.
        expect(heading).toBeVisible()
    })

    // BLOCKING-3 mutation check: no heading element sits inside any
    // `<summary>` on this page. Put an h2 back inside a `Disclosure`'s
    // `summary` prop and this test fails.
    it("never puts a heading (h1-h4) inside a <summary> on this page", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        const summaries = document.querySelectorAll("summary")
        expect(summaries.length).toBeGreaterThan(0)
        for (const summary of summaries) {
            expect(summary.querySelector("h1, h2, h3, h4")).toBeNull()
        }
    })

    it("keeps the evidence-ledger heading registered in the table of contents", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        const toc = await screen.findByRole("navigation", { name: "Sections on this page" })
        expect(within(toc).getByRole("link", { name: "Observation-scoped evidence (1 deposited observation)" }))
            .toBeVisible()
    })

    // The header used to be TITLED by the producer's own label (e.g.
    // "conformer_1", an ARC-assigned string, not TCKDB semantics) at a
    // 120px h1, then demoted to a secondary "Producer label" identity fact.
    // That fact is now REMOVED entirely (house rule widened 2026-09: no
    // depositor-typed labels on public pages at all) -- the h1 always
    // states what the record is, and the ref is the one identity fact this
    // page still shows. `payload.record.conformer_group.label` is still
    // deposited ("conformer_1", see the fixture above); it must never
    // surface.
    it("titles the record by what it is, lists the stable ref, and never shows the producer's own label", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        expect(screen.getByText("Group ref")).toBeVisible()
        expect(screen.getByText("cg_demo", { selector: "code" })).toBeVisible()
        expect(screen.queryByText("Producer label")).not.toBeInTheDocument()
        expect(screen.queryByText("conformer_1")).not.toBeInTheDocument()
    })

    // Owner decision: "yes show each record's own ref inline" -- this
    // page already showed "Group ref" first, but with no copy button;
    // every other record page's own-ref fact gets one via
    // `RecordIdentityHeader`'s `ownRef` prop, so this hand-rolled page
    // now matches that affordance.
    it("gives the group's own ref, first in the identity block, the same copy affordance as every other record page", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        const identityFacts = document.querySelector(".record-identity-header dl.kv-list") as HTMLElement
        expect(identityFacts).not.toBeNull()
        expect(Array.from(identityFacts.children)[0]).toHaveTextContent("Group ref")
        expect(screen.getByRole("button", { name: /copy group ref/i })).toBeVisible()
    })

    // Item 1/6/7, design/foundations PR B: the kicker-row/h1 order, the
    // review-status pill's `.value-pill` primitive (muted for
    // "not reviewed"), and the calculation table's `.data-table` primitive
    // are all mutation-checked here -- rename any of these classes and this
    // test fails.
    it("renders the kicker row before the h1, the review pill as .value-pill--muted, and the calculation table as .data-table", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        const h1 = await screen.findByRole("heading", { name: "Conformer basin" })
        // SHOULD-FIX-7 (PR B review): the kicker row, h1, intro and identity
        // `.kv-list` all sit inside the SAME `.record-identity-header`
        // wrapper `RecordIdentityHeader` itself renders -- not bare
        // siblings of `.basin-header` -- so this hand-matched header gets
        // the same `gap` between them. Mutation check: h1 must be a
        // descendant of `.record-identity-header`, and that wrapper's own
        // direct children must carry the kicker row before the h1.
        const wrapper = h1.closest(".record-identity-header") as HTMLElement
        expect(wrapper).not.toBeNull()
        const kickerRow = wrapper.querySelector(".record-identity-kicker-row") as HTMLElement
        expect(kickerRow).not.toBeNull()
        const order = Array.from(wrapper.children)
        expect(order.indexOf(kickerRow)).toBeLessThan(order.indexOf(h1))

        const pill = within(kickerRow).getByText("not reviewed")
        expect(pill).toHaveClass("value-pill")
        expect(pill).toHaveClass("value-pill--muted")
        expect(document.querySelector(".review-badge")).toBeNull()

        expect(document.querySelector(".stage-table")).toBeNull()
        const table = document.querySelector("table") as HTMLElement
        expect(table).toHaveClass("data-table")
        expect(table.closest(".table-scroll")).not.toBeNull()
    })

    it("omits the 'Producer label' row when the group has no deposited label either, but still shows the ref", async () => {
        const noLabelPayload = {
            record: {
                ...payload.record,
                conformer_group: { ...payload.record.conformer_group, label: null },
            },
        }
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(noLabelPayload)))
        page()
        // The h1 states what the record is either way -- it never falls
        // back to the ref or the (now absent) label.
        await screen.findByRole("heading", { name: "Conformer basin" })
        expect(screen.getByText("Group ref")).toBeVisible()
        expect(screen.getByText("cg_demo", { selector: "code" })).toBeVisible()
        expect(screen.queryByText("Producer label")).not.toBeInTheDocument()
    })

    // SF-1 (post-review): this page hand-rolls its own identity markup
    // (it does not compose `RecordIdentityHeader`, see this file's own
    // comment above `.basin-header`), so it grew its OWN copy of the
    // `species_entry_label` bug independently of that component --
    // `species.species_entry_label` rendered directly as the "Species
    // entry" fact's link text, a bare "R" on a real record. Now
    // delegates to `SpeciesEntryLink`, the same shared component
    // `RecordIdentityHeader.tsx` and `ConformerObservationPage.tsx` use,
    // which pairs the entry's served `formula` (RDKit-derived, backend
    // fix) with the discriminator expanded via `stereoChip` ("R" -> "R
    // enantiomer") rather than showing it raw.
    // `getByRole(..., { name })` uses accessible-name computation, which
    // collapses whitespace between the SMILES `code` and the bracketed-
    // formula `span` differently from raw `textContent` -- queried by
    // `href` and asserted on `textContent` here instead (see
    // `RecordIdentityHeader.test.tsx`'s identical comment).
    it("shows SMILES-leads-formula-in-brackets plus the EXPANDED label ('R' -> 'R enantiomer') instead of the raw discriminator", async () => {
        const labelledPayload = {
            record: {
                ...payload.record,
                species: { ...payload.record.species, species_entry_label: "R" },
            },
        }
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(labelledPayload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        const identityDl = document.querySelector(".basin-header dl.kv-list") as HTMLElement
        const link = identityDl.querySelector('a[href="/species-entries/spe_demo"]')!
        expect(link.textContent).toBe("[CH3] (CH3) · R enantiomer")
        // Never the bare raw token as the whole link text.
        expect(link.textContent).not.toBe("R")
    })

    it("shows SMILES-leads-formula-in-brackets alone with no label suffix when the entry has no deposited label", async () => {
        const noLabelPayload = {
            record: {
                ...payload.record,
                species: { ...payload.record.species, species_entry_label: null },
            },
        }
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(noLabelPayload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        const identityDl = document.querySelector(".basin-header dl.kv-list") as HTMLElement
        const link = identityDl.querySelector('a[href="/species-entries/spe_demo"]')!
        expect(link.textContent).toBe("[CH3] (CH3)")
    })

    // Unified fallback rule (per this component's own reviewer-flagged
    // duplication fix): when the species SMILES did not parse and the
    // backend serves neither a `formula` nor a `canonical_smiles`, the
    // base link text is the entry REF as `<code className="data">`,
    // never the literal words "Species entry" -- the `<dt>` beside this
    // `<dd>` already says that.
    it("falls back to the entry ref, as a data code run, when the species context carries no SMILES and no formula", async () => {
        const noFormulaPayload = {
            record: {
                ...payload.record,
                species: { ...payload.record.species, canonical_smiles: null, formula: null, species_entry_label: null },
            },
        }
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(noFormulaPayload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        const identityDl = document.querySelector(".basin-header dl.kv-list") as HTMLElement
        const link = within(identityDl).getByRole("link", { name: "spe_demo" })
        expect(link).toHaveAttribute("href", "/species-entries/spe_demo")
        const code = link.querySelector("code")
        expect(code).not.toBeNull()
        expect(code).toHaveClass("data")
        expect(link.textContent).not.toContain("Species entry")
    })

    it("carries the TCKDB / Species / Species entry / Conformer basin breadcrumb -- the record page this was reported missing it on", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(breadcrumb).getByRole("link", { name: "TCKDB" })).toHaveAttribute("href", "/")
        expect(within(breadcrumb).getByRole("link", { name: "Species" }))
            .toHaveAttribute("href", "/species/spc_demo")
        expect(within(breadcrumb).getByRole("link", { name: "Species entry" }))
            .toHaveAttribute("href", "/species-entries/spe_demo")
        expect(within(breadcrumb).getByText("Conformer basin")).toHaveAttribute("aria-current", "page")
    })

    it("shows a plain, always-open empty state when the group has no deposited observations (nothing to disclose)", async () => {
        const emptyPayload = {
            record: {
                ...payload.record,
                observations_summary: { total: 0, by_scientific_origin: {} },
                observations: [],
            },
        }
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(emptyPayload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        expect(screen.getByRole("heading", { name: "Observation-scoped evidence" })).toBeVisible()
        expect(screen.getByText("No deposited observations were returned for this conformer basin."))
            .toBeVisible()
        // No disclosure control offered over an EMPTY observation-scoped
        // evidence section specifically -- scoped to that section, not the
        // whole document: the page's own `EvidenceChecklist` coverage card
        // (a DIFFERENT, always-present disclosure, per item 2) still
        // renders its own `details.disclosure` regardless of whether this
        // basin has any observations at all.
        const observationSection = document.querySelector('section[aria-labelledby="observation-ledger"]') as HTMLElement
        expect(observationSection.querySelector("details.disclosure")).toBeNull()
    })

    // This page renders exactly 2 sections at runtime (observation-scoped
    // evidence, geometry records) -- at the shared shell's list threshold
    // (`MIN_SECTIONS_FOR_LIST`, `components/TableOfContents.tsx`), so the
    // table of contents DOES show here: a list is worth showing once there
    // is more than one place to jump to. Real fixture, real page -- not a
    // count hand-picked to land at the threshold.
    it("renders a table of contents with both of this page's sections -- 2 sections is at the list threshold", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        const toc = await screen.findByRole("navigation", { name: "Sections on this page" })
        await waitFor(() => expect(within(toc).getAllByRole("link")).toHaveLength(2))
    })

    it("shows a specific not-found state", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => {
            return HttpResponse.json({}, { status: 404 })
        }))
        page()
        expect(await screen.findByRole("heading", { name: "Conformer basin not found" })).toBeVisible()
    })

    // Regression test for the requestCache StrictMode bug (see the module
    // docstring on `strictPage`): the SAME 404 handler as the test above,
    // under StrictMode, still has to reach the real "not found"
    // classification -- not the generic "unavailable" a misattributed
    // AbortError used to produce (the discarded probe mount's own cleanup
    // aborted the ONE real request before the surviving mount's effect
    // ever received the 404).
    it("shows the same specific not-found state under React.StrictMode's double-invoked effects", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => {
            return HttpResponse.json({}, { status: 404 })
        }))
        strictPage()
        expect(await screen.findByRole("heading", { name: "Conformer basin not found" })).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Conformer basin unavailable" })).not.toBeInTheDocument()
    })

    // Same bug, the OTHER failure mode: a page that renders successfully
    // never resolves at all under StrictMode -- stuck on its loading state
    // forever, because the discarded probe mount's cleanup aborted the one
    // real (successful) request before the surviving mount could observe
    // it.
    it("renders real data under React.StrictMode's double-invoked effects, never stuck loading", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        strictPage()
        expect(await screen.findByRole("heading", { name: "Conformer basin" })).toBeVisible()
        expect(screen.queryByText("Loading conformer basin…")).not.toBeInTheDocument()
    })

    it("gives a malformed-ref 422 (code invalid_handle) its own non-retryable state", async () => {
        // This surface previously had no 422 coverage at all. `invalid_handle`
        // (distinct from `handle_type_mismatch`, the only code every other
        // page's 422 test exercised) is what live traffic actually returns
        // for a malformed-but-right-prefix ref — pinning it here guards the
        // `INVALID_HANDLE_CODES` classification in `useScientificRecord`,
        // shared machinery this page depends on but does not own.
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json({
            code: "invalid_handle",
            detail: "invalid_handle: 'cg_' not a recognised conformer_group handle",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a conformer basin reference" })).toBeVisible()
        expect(screen.getByText(/not a recognised conformer_group handle/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    // Review follow-up (SHOULD-FIX #1): the automatic `Retry-After` wait
    // (`requestScientificJson`) can run up to a minute
    // (`rate_limit_anon_read_per_minute`, `backend/app/api/config.py`) --
    // during it, the page must say "the archive is busy, retrying
    // automatically", not sit on a plain, indefinite "Loading …" a reader
    // has no way to tell apart from a stuck page.
    it("shows a distinct 'archive is busy' state during the automatic Retry-After wait, then renders real data once the retry succeeds", async () => {
        vi.useFakeTimers()
        let attempt = 0
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => {
            attempt += 1
            if (attempt === 1) {
                return HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "8" } })
            }
            return HttpResponse.json(payload)
        }))
        page()

        // Let the first (429) response and its synchronous onRateLimited
        // notification land, without yet advancing past the retry delay.
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        expect(screen.getByRole("heading", { name: "Loading conformer basin…" })).toBeVisible()
        const waitingMessage = () => screen.getByText(/receiving too many requests right now/)
        expect(waitingMessage()).toBeVisible()
        expect(waitingMessage().textContent).toMatch(/retrying automatically in about 8 seconds…/i)
        // Never the terminal states while still waiting on the automatic retry.
        expect(screen.queryByRole("heading", { name: "Conformer basin unavailable" })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Archive is busy" })).not.toBeInTheDocument()

        // The countdown ticks down locally (RetryCountdown), independent
        // of the retry itself actually firing.
        await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
        expect(waitingMessage().textContent).toMatch(/retrying automatically in about 5 seconds…/i)

        // The retry itself fires at the full Retry-After delay and succeeds.
        await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
        expect(screen.getByRole("heading", { name: "Conformer basin" })).toBeVisible()
        expect(attempt).toBe(2)
    })

    // Wording pin (SHOULD-FIX #3): plain language, not operator vocabulary
    // -- "about 30 seconds", never the abbreviated "30s" the pre-fix copy
    // used ("Wait about 2s and try again").
    it("uses plain-language wording for the terminal rate-limited state, spelling out the wait", async () => {
        vi.useFakeTimers()
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => (
            HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "30" } })
        )))
        page()
        await act(async () => { await vi.advanceTimersByTimeAsync(0) }) // first attempt lands, retry scheduled
        await act(async () => { await vi.advanceTimersByTimeAsync(30_000) }) // the retry itself lands, also 429

        expect(screen.getByRole("heading", { name: "Archive is busy" })).toBeVisible()
        const message = screen.getByText(/receiving too many requests right now/)
        expect(message).toHaveTextContent(
            "The archive is receiving too many requests right now. Wait about 30 seconds and reload the page.",
        )
        // Never the abbreviated, operator-vocabulary form.
        expect(message.textContent).not.toMatch(/\d+s\b/)
    })
})
