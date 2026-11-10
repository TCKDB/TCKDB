import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import App from "../App"
import ArchiveHomePage from "./ArchiveHomePage"

const ENTRY = "spe_abcdefghijklmnopqrstuvwxyz"
const REACTION_ENTRY = "rxe_abcdefghijklmnopqrstuvwxyz"
const server = setupServer(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

function envelope(total: number) {
    return { records: [], pagination: { offset: 0, limit: 1, returned: 0, total } }
}

type Totals = { molecules?: number | "fail"; reactions?: number | "fail"; levels?: number | "fail" }

/** The three count reads. A total of "fail" answers 500. The calls made are recorded. */
function countHandlers(totals: Totals = {}, calls: string[] = [], onOther: (query: string) => void = () => {}) {
    const answer = (name: string, total: number | "fail" | undefined, fallback: number) => {
        calls.push(name)
        return total === "fail" ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(envelope(total ?? fallback))
    }
    const oneRow = (request: Request) => expect(new URL(request.url).searchParams.get("limit")).toBe("1")
    return [
        configJson(null),
        http.get("/api/v1/scientific/species/browse", ({ request }) => { oneRow(request); return answer("molecules", totals.molecules, 2481) }),
        http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
            // The reaction search reads this endpoint too, with its own page size.
            if (new URL(request.url).searchParams.get("limit") !== "1") { onOther(new URL(request.url).search); return HttpResponse.json(envelope(0)) }
            return answer("reactions", totals.reactions, 1104)
        }),
        http.get("/api/v1/scientific/level-of-theories/browse", ({ request }) => { oneRow(request); return answer("levels", totals.levels, 41) }),
    ]
}

/** The operator's site config: a ref, or a 404 when none. */
function configJson(ref: string | null) {
    return http.get("/site-config.json", ({ request }) => {
        expect(request.cache).toBe("no-cache")
        return ref === null ? new HttpResponse(null, { status: 404 }) : HttpResponse.json({ homeExampleRef: ref })
    })
}

const summary = { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 }
const lot = { method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp", level_of_theory_ref: "lot_a" }

function thermoRecord(ref: string, h298: number) {
    return {
        thermo_ref: ref, scientific_origin: "computed", model_kind: "nasa", review: { status: "not_reviewed" },
        h298_kj_mol: h298, s298_j_mol_k: 200.5, enthalpy_reference_kind: "formation_298k",
        thermodynamic_target: { kind: "equilibrium_ensemble", conformer_group_ref: null, declared_by: "depositor" },
        phase: "gas", reference_pressure_bar: 1,
        temperature_coverage: { record_min_k: 300, record_max_k: 2000, covers_requested_range: true, extrapolation_distance_k: 0 },
        levels: { geometry: lot, frequency: lot, energy: lot, energy_source: "opt" },
        provenance: { software_release: { software_release_ref: "srel", software: "Arkane", version: "1.1.0" }, conformer_group_ref: null },
    }
}

function page(initial = "/") {
    return render(<MemoryRouter initialEntries={[initial]}><ArchiveHomePage /></MemoryRouter>)
}

/** The example molecule: the entry lookup plus the three lists the numbers block reads. */
function exampleHandlers(found: boolean, records = 1) {
    const list = (rows: unknown[], request: Request) => {
        const size = Number(new URL(request.url).searchParams.get("limit") ?? 50)
        const page = rows.slice(0, size)
        return HttpResponse.json({
            species_entry_ref: ENTRY, review_summary: summary, records: page, pagination: { offset: 0, limit: size, returned: page.length, total: rows.length },
        })
    }
    return [
        http.get("/api/v1/scientific/species/search", ({ request }) => {
            const query = new URL(request.url).searchParams
            expect(query.get("species_entry_ref")).toBe(ENTRY)
            expect(query.getAll("include")).toEqual([])
            return HttpResponse.json(found ? { records: [{
                species_ref: "spc_numbers", canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3", charge: 0, multiplicity: 2,
                entries: [{
                    species_entry_ref: ENTRY, species_entry_kind: "minimum", electronic_state_kind: "ground", species_entry_label: null,
                    review: { status: "not_reviewed" },
                    availability: { has_thermo: true, has_statmech: false, has_transport: false, has_conformers: false, calculation_count: 1 },
                }],
            }] } : { records: [] })
        }),
        http.get(`/api/v1/scientific/species-entries/${ENTRY}/thermo`, ({ request }) => {
            // The home card asks for one short page, never the whole list.
            expect(new URL(request.url).searchParams.get("limit")).toBe("2")
            return list(Array.from({ length: records }, (_, index) => thermoRecord(`thm_${index}`, 100 + index)), request)
        }),
        http.get(`/api/v1/scientific/species-entries/${ENTRY}/statmech`, ({ request }) => list([], request)),
        http.get(`/api/v1/scientific/species-entries/${ENTRY}/transport`, ({ request }) => list([], request)),
    ]
}

describe("ArchiveHomePage: the search", () => {
    it("renders the exact-identifier search, defaulting to species mode, and no structure search", () => {
        server.use(...countHandlers())
        page()
        expect(screen.getByLabelText("Molecule: exact SMILES, formula, InChI, InChIKey or TCKDB ref")).toBeInTheDocument()
        expect(screen.getByRole("radio", { name: "Molecules" })).toHaveAttribute("aria-checked", "true")
        expect(screen.getByRole("radio", { name: "Reactions" })).toHaveAttribute("aria-checked", "false")
        expect(screen.queryByText("Structure search")).not.toBeInTheDocument()
        expect(screen.queryByLabelText(/SMARTS/)).not.toBeInTheDocument()
    })

    it("has one h1, a plain description and no ranking or verification claim", () => {
        server.use(...countHandlers())
        page()
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
        const text = document.body.textContent ?? ""
        expect(text).not.toMatch(/verified|best|recommended by/i)
        expect(text).not.toContain("--")
        expect(text).toContain("level of theory")
    })

    it("offers Try links that hand the query to the search", () => {
        server.use(...countHandlers())
        page()
        const links = within(screen.getByText("Try").closest("div") as HTMLElement).getAllByRole("link")
        expect(links.map((link) => link.textContent)).toEqual(["CH4", "[OH]", "O=O", "[CH3],[OH] <> CO"])
        expect(links.map((link) => link.getAttribute("href"))).toEqual([
            "/?q=CH4", "/?q=%5BOH%5D", "/?q=O%3DO", `/?q=${encodeURIComponent("[CH3],[OH] <> CO")}`,
        ])
    })
})

describe("ArchiveHomePage: Try links run the search", () => {
    function searchCalls() {
        const calls = { species: [] as string[], reactions: [] as string[] }
        server.use(
            ...countHandlers({}, [], (query) => calls.reactions.push(query)),
            http.get("/api/v1/scientific/species/structure-search", ({ request }) => {
                calls.species.push(new URL(request.url).search)
                return HttpResponse.json({ records: [] })
            }),
            http.get("/api/v1/scientific/reactions/search", ({ request }) => {
                calls.reactions.push(new URL(request.url).search)
                return HttpResponse.json({ records: [], pagination: { offset: 0, limit: 20, returned: 0, total: 0 } })
            }),
        )
        return calls
    }

    it("a molecule link fills the field and sends one molecule search", async () => {
        const calls = searchCalls()
        const user = userEvent.setup()
        render(<App />)
        await user.click(await screen.findByRole("link", { name: "[OH]" }))
        await waitFor(() => expect(calls.species).toHaveLength(1))
        expect(calls.species[0]).toContain(encodeURIComponent("[OH]"))
        expect(document.getElementById("identifier")).toHaveValue("[OH]")
        expect(window.location.search).toBe("")
        expect(calls.reactions).toHaveLength(0)
    })

    it("the reaction link searches reactions", async () => {
        const calls = searchCalls()
        const user = userEvent.setup()
        render(<App />)
        await user.click(await screen.findByRole("link", { name: "[CH3],[OH] <> CO" }))
        await waitFor(() => expect(calls.reactions).toHaveLength(1))
        expect(calls.reactions[0]).toContain("reactant_smiles=%5BCH3%5D")
        expect(calls.reactions[0]).toContain("reactant_smiles=%5BOH%5D")
        expect(calls.reactions[0]).toContain("product_smiles=CO")
        expect(screen.getByRole("radio", { name: "Reactions" })).toHaveAttribute("aria-checked", "true")
        expect(calls.species).toHaveLength(0)
    })
})

async function strip(): Promise<HTMLElement> {
    return waitFor(() => {
        const found = document.querySelector<HTMLElement>(".home-counts")
        if (!found) throw new Error("no counts strip yet")
        return found
    })
}

describe("ArchiveHomePage: counts come only from reads", () => {
    it("shows each server total, formatted, in the strip and on the cards", async () => {
        server.use(...countHandlers({ molecules: 12345, reactions: 7, levels: 3 }))
        page()
        const counts = await strip()
        await waitFor(() => expect(within(counts).getByText("12,345")).toBeInTheDocument())
        expect(within(counts).getByText("Molecules")).toBeInTheDocument()
        expect(within(counts).getByText("7")).toBeInTheDocument()
        expect(within(counts).getByText("Reaction entries")).toBeInTheDocument()
        expect(within(counts).getByText("3")).toBeInTheDocument()
        expect(within(counts).getByText("Levels of theory")).toBeInTheDocument()
        const cards = document.querySelector(".home-cards") as HTMLElement
        expect(cards).toHaveTextContent("12,345 molecules")
        expect(cards).toHaveTextContent("7 reaction entries")
        expect(cards).toHaveTextContent("3 levels of theory")
    })

    it("leaves out a count whose read failed, in the strip and on its card, and keeps the others", async () => {
        server.use(...countHandlers({ levels: "fail" }))
        page()
        const counts = await strip()
        await waitFor(() => expect(within(counts).getByText("2,481")).toBeInTheDocument())
        expect(within(counts).getByText("1,104")).toBeInTheDocument()
        expect(within(counts).queryByText("Levels of theory")).not.toBeInTheDocument()
        expect(document.querySelector(".home-cards")).not.toHaveTextContent(/\d levels of theory/)
        expect(screen.getByRole("link", { name: "Methods" })).toHaveAttribute("href", "/methods")
    })

    it("shows no strip when every read fails, and invents no number", async () => {
        server.use(...countHandlers({ molecules: "fail", reactions: "fail", levels: "fail" }))
        page()
        await waitFor(() => expect(document.querySelector(".home-counts")).toBeNull())
        expect(document.querySelector(".home-cards")?.textContent).not.toMatch(/\d/)
    })

    it("asks each count read for one row, once", async () => {
        const calls: string[] = []
        server.use(...countHandlers({}, calls))
        page()
        await waitFor(() => expect(calls.sort()).toEqual(["levels", "molecules", "reactions"]))
    })
})

describe("ArchiveHomePage: browse cards", () => {
    it("links the three indexes", () => {
        server.use(...countHandlers())
        page()
        expect(screen.getByRole("link", { name: "Molecules" })).toHaveAttribute("href", "/species")
        expect(screen.getByRole("link", { name: "Reactions" })).toHaveAttribute("href", "/reactions")
        expect(screen.getByRole("link", { name: "Methods" })).toHaveAttribute("href", "/methods")
    })

    it("says methods are listed in no particular order of quality", () => {
        server.use(...countHandlers())
        page()
        expect(document.querySelector(".home-cards")).toHaveTextContent("no particular order of quality")
    })
})

describe("ArchiveHomePage: how to read a value", () => {
    it("explains the number, the calculation, the level of theory and the review status, and marks the sample as an illustration", () => {
        server.use(...countHandlers())
        page()
        const section = screen.getByRole("heading", { name: "How to read a value" }).closest("section") as HTMLElement
        const steps = within(section).getAllByRole("listitem")
        expect(steps).toHaveLength(4)
        expect(steps[0]).toHaveTextContent("uncertainty")
        expect(steps[1]).toHaveTextContent("calculation")
        expect(steps[2]).toHaveTextContent("level of theory")
        expect(steps[3]).toHaveTextContent("review status")
        expect(within(section).getByText(/Illustration with placeholder wording/)).toBeInTheDocument()
        const figure = section.querySelector("figure") as HTMLElement
        const sample = figure.cloneNode(true) as HTMLElement
        sample.querySelectorAll(".home-callout").forEach((callout) => callout.remove())
        expect(sample.textContent).not.toMatch(/\d/)
    })
})

describe("ArchiveHomePage: the example record", () => {
    const COPY = "An example record, chosen by the site editors to show how a record reads. It is not a recommendation."

    async function settled() {
        await waitFor(() => expect(document.querySelector(".home-counts")).not.toBeNull())
    }

    it("shows the Browse molecules link and no card when no example is set", async () => {
        server.use(...countHandlers())
        page()
        await settled()
        expect(screen.getByRole("link", { name: "Browse molecules" })).toHaveAttribute("href", "/species")
        expect(screen.queryByText(COPY)).not.toBeInTheDocument()
        expect(screen.queryByRole("article", { name: "Example record" })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Example record" })).not.toBeInTheDocument()
        expect(screen.queryByRole("link", { name: /See an example/ })).not.toBeInTheDocument()
    })

    it("shows the card with the editors' copy for a molecule the archive returns", async () => {
        server.use(configJson(ENTRY), ...countHandlers(), ...exampleHandlers(true))
        page()
        const card = await screen.findByRole("article", { name: "Example record" })
        expect(screen.getByText(COPY)).toBeInTheDocument()
        expect(within(card).getByRole("link", { name: "Open this molecule" })).toHaveAttribute("href", `/species-entries/${ENTRY}`)
        expect(within(card).getByText(ENTRY)).toBeInTheDocument()
        expect(await within(card).findByRole("heading", { name: "Numbers for this entry" })).toBeInTheDocument()
        await waitFor(() => expect(card.textContent).toContain("thm_0"), { timeout: 2000 }).catch(() => { throw new Error(card.textContent ?? "") })
        expect(within(card).getAllByRole("heading").every((heading) => heading.tagName === "H3")).toBe(true)
        expect(screen.queryByRole("link", { name: "Browse molecules" })).not.toBeInTheDocument()
    })

    it("shows only the first records of a long list and links to the molecule page", async () => {
        server.use(configJson(ENTRY), ...countHandlers(), ...exampleHandlers(true, 9))
        page()
        const card = await screen.findByRole("article", { name: "Example record" })
        expect(await within(card).findByText(/Showing the first 2 of 9 records/)).toBeInTheDocument()
        expect(card.querySelectorAll("tbody tr[id]")).toHaveLength(2)
        expect(within(card).getByRole("link", { name: "All records on the molecule page" })).toHaveAttribute("href", `/species-entries/${ENTRY}`)
    })

    it("falls back to Browse molecules when the archive does not return the entry (not found or hidden)", async () => {
        server.use(configJson(ENTRY), ...countHandlers(), ...exampleHandlers(false))
        page()
        expect(await screen.findByRole("link", { name: "Browse molecules" })).toBeInTheDocument()
        expect(screen.queryByText(COPY)).not.toBeInTheDocument()
        expect(screen.queryByRole("article", { name: "Example record" })).not.toBeInTheDocument()
    })

    it("offers a See an example link under the search when an example is set", async () => {
        server.use(configJson(ENTRY), ...countHandlers(), ...exampleHandlers(true))
        page()
        const link = await screen.findByRole("link", { name: /See an example/ })
        expect(link).toHaveAttribute("href", `/species-entries/${ENTRY}`)
        expect(link).toHaveTextContent("See an example: CH3 →")
    })

    it("falls back to Browse molecules when the rate limit stops the molecule read", async () => {
        server.use(
            configJson(ENTRY),
            ...countHandlers(),
            http.get("/api/v1/scientific/species/search", () => new HttpResponse(null, { status: 429, headers: { "Retry-After": "1" } })),
        )
        page()
        expect(await screen.findByRole("link", { name: "Browse molecules" }, { timeout: 4000 })).toBeInTheDocument()
        expect(screen.queryByText(COPY)).not.toBeInTheDocument()
    })

    it("falls back to Browse molecules when the rate limit stops the reaction read", async () => {
        server.use(
            configJson(REACTION_ENTRY),
            ...countHandlers(),
            http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY}/full`, () => new HttpResponse(null, { status: 429, headers: { "Retry-After": "1" } })),
        )
        page()
        expect(await screen.findByRole("link", { name: "Browse molecules" }, { timeout: 4000 })).toBeInTheDocument()
        expect(screen.queryByText(COPY)).not.toBeInTheDocument()
    })

    it.each([
        ["a missing file", () => new HttpResponse(null, { status: 404 })],
        ["an HTML answer", () => new HttpResponse("<html></html>", { headers: { "content-type": "text/html" } })],
        ["a file that is not JSON", () => new HttpResponse("{nope", { headers: { "content-type": "application/json" } })],
        ["a ref that is not an entry ref", () => HttpResponse.json({ homeExampleRef: "spc_abcdefghijklmnopqrstuvwxyz" })],
        ["no ref", () => HttpResponse.json({})],
    ])("shows Browse molecules and makes no record read for %s", async (_name, answer) => {
        const reads: string[] = []
        server.use(
            http.get("/site-config.json", answer),
            ...countHandlers(),
            http.get("/api/v1/scientific/species/search", ({ request }) => { reads.push(request.url); return HttpResponse.json({ records: [] }) }),
        )
        page()
        expect(await screen.findByRole("link", { name: "Browse molecules" })).toBeInTheDocument()
        expect(reads).toEqual([])
    })

    it("shows Browse molecules when the config request fails on the network", async () => {
        server.use(http.get("/site-config.json", () => HttpResponse.error()), ...countHandlers())
        page()
        expect(await screen.findByRole("link", { name: "Browse molecules" })).toBeInTheDocument()
    })

    it("falls back when the archive returns the parent species with no entries (the hidden shape)", async () => {
        server.use(
            configJson(ENTRY),
            ...countHandlers(),
            http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{
                species_ref: "spc_numbers", canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3", charge: 0, multiplicity: 2, entries: [],
            }] })),
        )
        page()
        expect(await screen.findByRole("link", { name: "Browse molecules" })).toBeInTheDocument()
        expect(screen.queryByText(COPY)).not.toBeInTheDocument()
        expect(screen.queryByRole("link", { name: /See an example/ })).not.toBeInTheDocument()
    })

    it("falls back when the species returned lists only other entries", async () => {
        server.use(
            configJson(ENTRY),
            ...countHandlers(),
            http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{
                species_ref: "spc_numbers", canonical_smiles: "[CH3]", formula: "CH3", entries: [{ species_entry_ref: "spe_other" }],
            }] })),
        )
        page()
        expect(await screen.findByRole("link", { name: "Browse molecules" })).toBeInTheDocument()
        expect(screen.queryByText(COPY)).not.toBeInTheDocument()
    })

    it("falls back when the read fails, and never picks a latest record", async () => {
        const sorts: string[] = []
        server.use(
            configJson(ENTRY),
            ...countHandlers(),
            http.get("/api/v1/scientific/species/search", () => new HttpResponse(null, { status: 500 })),
            http.get("/api/v1/scientific/species-entries/:ref/thermo", ({ request }) => { sorts.push(request.url); return HttpResponse.json({}) }),
        )
        page()
        expect(await screen.findByRole("link", { name: "Browse molecules" })).toBeInTheDocument()
        expect(screen.queryByText(COPY)).not.toBeInTheDocument()
        expect(sorts).toEqual([])
    })

    it("shows a reaction example as its equation with a link to the reaction entry", async () => {
        const participant = (ref: string, smiles: string, formula: string) => ({
            species_entry_ref: ref, smiles, formula, stoichiometry: 1, participant_index: 0, review: { status: "not_reviewed" },
        })
        server.use(configJson(REACTION_ENTRY), ...countHandlers(), http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY}/full`, () => HttpResponse.json({
            reaction_entry: {
                reaction_entry_ref: REACTION_ENTRY, reaction_ref: "rxn_test1", equation: "[CH3] <=> C", reversible: false,
                family: null, review: { status: "not_reviewed" }, atom_maps: [],
            },
            review_summary: summary,
            species: { reactants: [participant("spe_a", "[CH3]", "CH3")], products: [participant("spe_b", "C", "CH4")] },
        })))
        page()
        const card = await screen.findByRole("article", { name: "Example record" })
        expect(within(card).getByRole("link", { name: "Open this reaction" })).toHaveAttribute("href", `/reaction-entries/${REACTION_ENTRY}`)
    })

    it("falls back to Browse molecules when the reaction entry is not found", async () => {
        server.use(configJson(REACTION_ENTRY), ...countHandlers(), http.get(`/api/v1/scientific/reaction-entries/${REACTION_ENTRY}/full`, () => HttpResponse.json({ detail: "none" }, { status: 404 })))
        page()
        expect(await screen.findByRole("link", { name: "Browse molecules" })).toBeInTheDocument()
        expect(screen.queryByText(COPY)).not.toBeInTheDocument()
    })
})

describe("ArchiveHomePage: keyboard order", () => {
    it("reaches the skip link, the search box, each Try link, then the example card's links", async () => {
        server.use(configJson(ENTRY), ...countHandlers(), ...exampleHandlers(true))
        const user = userEvent.setup()
        render(<App />)
        await screen.findByRole("article", { name: "Example record" })
        const order: string[] = []
        for (let step = 0; step < 80; step += 1) {
            await user.tab()
            const active = document.activeElement as HTMLElement
            const label = active.id === "identifier" ? "search box" : active.classList.contains("skip-link") ? "skip link" : (active.textContent ?? "").trim()
            order.push(label)
        }
        const expected = ["skip link", "search box", "CH4", "[OH]", "O=O", "[CH3],[OH] <> CO", "See an example: CH3 →", "Open this molecule"]
        let cursor = -1
        for (const label of expected) {
            const index = order.indexOf(label, cursor + 1)
            expect(index, `${label} after position ${cursor}`).toBeGreaterThan(cursor)
            cursor = index
        }
        expect(order.indexOf("Molecules", order.indexOf("[CH3],[OH] <> CO"))).toBeLessThan(order.indexOf("Open this molecule"))
    })
})
