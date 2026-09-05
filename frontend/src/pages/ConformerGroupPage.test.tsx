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
        expect(screen.getByText("3")).toBeVisible()
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
    // 120px h1. The h1 now always states what the record is; the ref is
    // always shown as its own identity row, and a deposited producer label
    // -- when there is one -- is a separate, secondary row next to it,
    // never a substitute for either.
    it("titles the record by what it is, and lists both the stable ref and the producer's own label as separate identity facts", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "Conformer basin" })
        expect(screen.getByText("Group ref")).toBeVisible()
        expect(screen.getByText("cg_demo", { selector: "code" })).toBeVisible()
        expect(screen.getByText("Producer label")).toBeVisible()
        expect(screen.getByText("conformer_1", { selector: "dd" })).toBeVisible()
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

    it("omits the 'Producer label' row when the group has no deposited label, but still shows the ref", async () => {
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
    // entry" fact's link text, a bare "R" on a real record. Fixed the
    // same way `RecordIdentityHeader.tsx`'s "Species entry" fact is:
    // `stereoChip` expands the served discriminator ("R" -> "R
    // enantiomer") rather than showing it raw. This endpoint's `species`
    // context carries no `formula` at all (this file's own comment above
    // `.basin-header` documents that), so the base text is always the
    // literal "Species entry" here, with the expanded label appended
    // when one was served.
    it("expands the species-entry label via stereoChip instead of showing the raw discriminator ('R' -> 'R enantiomer')", async () => {
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
        const link = within(identityDl).getByRole("link", { name: "Species entry · R enantiomer" })
        expect(link).toHaveAttribute("href", "/species-entries/spe_demo")
        // Never the bare raw token as the whole link text.
        expect(within(identityDl).queryByRole("link", { name: "R" })).not.toBeInTheDocument()
    })

    it("shows the literal 'Species entry' with no label suffix when the entry has no deposited label", async () => {
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
        expect(within(identityDl).getByRole("link", { name: "Species entry" })).toHaveAttribute("href", "/species-entries/spe_demo")
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
        // No disclosure control offered over an empty section.
        expect(document.querySelector("details.disclosure")).toBeNull()
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
