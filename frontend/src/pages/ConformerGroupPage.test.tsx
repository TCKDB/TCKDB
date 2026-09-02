import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ConformerGroupPage from "./ConformerGroupPage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
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
        expect(await screen.findByRole("heading", { name: "conformer_1" })).toBeVisible()
        expect(screen.getByText(/One torsional basin, shown through its deposited observations/))
            .toBeVisible()
        expect(screen.getByText("3")).toBeVisible()
        expect(screen.getByText("1 optimisation chains")).toBeVisible()

        // The observation-scoped evidence ledger is now a collapsed-by-default
        // disclosure -- open it before asserting on anything inside it.
        fireEvent.click(screen.getByRole("heading", { name: "Observation-scoped evidence (1 deposited observation)" }))

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

    it("collapses the observation-scoped evidence ledger by default, naming its count, and expands it via the keyboard", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "conformer_1" })

        const heading = screen.getByRole("heading", { name: "Observation-scoped evidence (1 deposited observation)" })
        const details = heading.closest("details")
        const summary = heading.closest("summary")
        expect(details).not.toBeNull()
        expect(summary).not.toBeNull()

        // Collapsed by default: the heading itself is visible (it says what
        // is inside, with the count), but the observation card behind it is
        // not -- jest-dom's `toBeVisible` treats non-summary content of a
        // closed <details> as not visible, mirroring what a sighted reader
        // sees before clicking.
        expect(heading).toBeVisible()
        expect(details).not.toHaveAttribute("open")
        expect(summary).toHaveAttribute("aria-expanded", "false")
        expect(screen.getByText("b3lyp/def2tzvp")).not.toBeVisible()

        // <summary> is a real, native, keyboard-operable disclosure control
        // -- Enter toggles a focused summary in every real browser, the
        // same underlying activation a click triggers. The native `open`
        // DOM attribute flips synchronously on click (content visibility
        // depends only on that, so it's already asserted below without
        // waiting); the `toggle` EVENT that drives this component's
        // controlled `aria-expanded` — via `onToggle` -> `setOpen` — fires
        // as its own task per spec (MEASURED in jsdom too), landing one
        // tick after the click returns, hence the `waitFor`.
        fireEvent.click(heading)

        expect(details).toHaveAttribute("open")
        expect(screen.getByText("b3lyp/def2tzvp")).toBeVisible()
        await waitFor(() => expect(summary).toHaveAttribute("aria-expanded", "true"))
    })

    it("keeps the evidence-ledger heading registered in the table of contents while collapsed", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        page()
        await screen.findByRole("heading", { name: "conformer_1" })
        // The disclosure starts collapsed (see the test above) -- the ToC
        // entry must still be there, because SectionHeading lives inside
        // <summary> and <details> never unmounts its children.
        const toc = await screen.findByRole("navigation", { name: "Sections on this page" })
        expect(within(toc).getByRole("link", { name: "Observation-scoped evidence (1 deposited observation)" }))
            .toBeVisible()
    })

    // Same shape as the fix on `CalculationDetailPage.tsx`'s `OwnerCard`: a
    // separate "Group ref" row is only needed when the heading above shows
    // a LABEL, not the ref itself. Without a label, the heading already
    // shows the ref (`basin.label ?? basin.conformer_group_ref`), and a
    // second row would repeat it.
    it("omits the duplicate 'Group ref' row when the group has no label (the heading already shows the ref)", async () => {
        const noLabelPayload = {
            record: {
                ...payload.record,
                conformer_group: { ...payload.record.conformer_group, label: null },
            },
        }
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(noLabelPayload)))
        page()
        // The h1 falls back to the ref, since there is no label to prefer.
        await screen.findByRole("heading", { name: "cg_demo" })
        expect(screen.queryByText("Group ref")).not.toBeInTheDocument()
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
        await screen.findByRole("heading", { name: "conformer_1" })
        expect(screen.getByRole("heading", { name: "Observation-scoped evidence" })).toBeVisible()
        expect(screen.getByText("No deposited observations were returned for this conformer basin."))
            .toBeVisible()
        // No disclosure control offered over an empty section.
        expect(document.querySelector("details.ledger-section")).toBeNull()
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
        await screen.findByRole("heading", { name: "conformer_1" })
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
})
