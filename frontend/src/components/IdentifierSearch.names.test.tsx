import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { IdentifierSearch } from "./IdentifierSearch"
import { Announcer } from "./Announcer"
import { resetNameSearchSession } from "../api/nameSearchApi"
import { looksLikeName } from "../domain/nameQuery"

const water = { species_ref: "spc_water", formula: "H2O", smiles: "O", name: "Water", kind: "common", source_kind: "offline_import" }
const waterVapour = { species_ref: "spc_vapour", formula: "H2O", smiles: "O", name: "Water vapour", kind: "common", source_kind: "curated" }

type Handler = (url: URL, init: RequestInit | undefined) => Response | Promise<Response>
let handler: Handler
let calls: { url: URL; init: RequestInit | undefined }[]

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } })
}

function nameBody(matches: unknown[]) {
    return { request: {}, matches, truncated: false, order: {} }
}

beforeEach(() => {
    vi.useFakeTimers()
    resetNameSearchSession()
    calls = []
    handler = () => json(nameBody([water, waterVapour]))
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(String(input), "http://localhost")
        calls.push({ url, init })
        return handler(url, init)
    })
})

afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
})

function page() {
    return render(
        <MemoryRouter initialEntries={["/"]}>
            <Routes>
                <Route path="/" element={<><IdentifierSearch /><Announcer /></>} />
                <Route path="/species/:ref" element={<div>landed on species</div>} />
            </Routes>
        </MemoryRouter>,
    )
}

function box() { return screen.getByRole("combobox") }
function typeText(text: string) { fireEvent.change(screen.getByLabelText(/^Molecule:/), { target: { value: text } }) }
async function advance(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
function nameCalls() { return calls.filter((call) => call.url.pathname.endsWith("/name-search")) }

describe("what counts as a name", () => {
    it("sends words to name search", () => {
        for (const text of ["water", "Water", "Hydrogen Peroxide", "1,2-ethanediol", "methane", "Methyl", "N-methylformamide"]) {
            expect(looksLikeName(text), text).toBe(true)
        }
    })

    it("keeps formulas, SMILES, InChI, InChIKeys and references on exact search", () => {
        for (const text of [
            "CH4", "H2O", "CCO", "CC(=O)O", "[OH]", "O=O", "c1ccccc1", "NO", "Co", "smiles:water", "formula:H2O",
            "InChI=1S/H2O/h1H2", "XLYOFNOQVPJJNP-UHFFFAOYSA-N", "spc_abcdefghijklmnopqrstuvwxyz", "rxn_abc",
        ]) {
            expect(looksLikeName(text), text).toBe(false)
        }
    })
})

describe("suggestions", () => {
    it("waits 300 ms after the last keystroke and asks for 8 prefix matches", async () => {
        page()
        typeText("wa"); await advance(200)
        typeText("wat"); await advance(299)
        expect(nameCalls()).toHaveLength(0)
        await advance(1)
        expect(nameCalls()).toHaveLength(1)
        const params = nameCalls()[0].url.searchParams
        expect([params.get("q"), params.get("match"), params.get("limit")]).toEqual(["wat", "prefix", "8"])
    })

    it("sends nothing for one character", async () => {
        page()
        typeText("w"); await advance(1000)
        expect(nameCalls()).toHaveLength(0)
    })

    it("sends nothing for a SMILES or a formula", async () => {
        page()
        for (const text of ["CCO", "CH4", "[OH]"]) { typeText(text); await advance(1000) }
        expect(calls).toHaveLength(0)
    })

    it("sends nothing for text the name rules would reject", async () => {
        page()
        typeText("water<script>"); await advance(1000)
        typeText("water?"); await advance(1000)
        typeText("w".repeat(65)); await advance(1000)
        typeText("--"); await advance(1000)
        expect(nameCalls()).toHaveLength(0)
    })

    it("cancels the request in flight when the text changes", async () => {
        handler = () => new Promise<Response>(() => { /* never settles */ })
        page()
        typeText("wat"); await advance(300)
        const first = nameCalls()[0].init?.signal as AbortSignal
        expect(first.aborted).toBe(false)
        typeText("wate"); await advance(10)
        expect(first.aborted).toBe(true)
    })

    it("remembers a query for the session instead of asking again", async () => {
        page()
        typeText("wat"); await advance(300)
        typeText("wa"); await advance(300)
        typeText("wat"); await advance(300)
        expect(nameCalls().filter((call) => call.url.searchParams.get("q") === "wat")).toHaveLength(1)
    })

    it("stops suggesting after a 429 until the retry time, and says so", async () => {
        handler = () => json({ detail: "slow down", code: "rate_limited" }, 429, { "Retry-After": "12" })
        page()
        typeText("wat"); await advance(300)
        expect(screen.getByText("Too many searches; try again in 12 seconds.")).toBeVisible()
        expect(nameCalls()).toHaveLength(1)

        typeText("wate"); await advance(2000)
        typeText("water"); await advance(2000)
        expect(nameCalls()).toHaveLength(1)
        expect(screen.getByText("Too many searches; try again in 8 seconds.")).toBeVisible()

        handler = () => json(nameBody([water]))
        await advance(8000)
        await advance(300)
        expect(nameCalls()).toHaveLength(2)
        expect(screen.queryByText(/Too many searches/)).not.toBeInTheDocument()
    })
})

describe("the suggestion list", () => {
    async function open(text = "wat") {
        page()
        typeText(text); await advance(300)
    }

    it("is a combobox with a listbox of options", async () => {
        await open()
        const input = box()
        const list = screen.getByRole("listbox")
        expect(input).toHaveAttribute("aria-expanded", "true")
        expect(input).toHaveAttribute("aria-controls", list.id)
        expect(screen.getAllByRole("option")).toHaveLength(2)
    })

    it("shows the name, then the formula, and a PubChem note only on an imported name", async () => {
        await open()
        const [imported, curated] = screen.getAllByRole("option")
        expect(imported).toHaveTextContent(/^Water.*H2O.*from PubChem$/)
        expect(curated).toHaveTextContent(/^Water vapour.*H2O$/)
        expect(curated).not.toHaveTextContent("PubChem")
    })

    it("says once, under the list, that the order is not a ranking", async () => {
        await open()
        expect(screen.getAllByText("Alphabetical, not a ranking")).toHaveLength(1)
    })

    it("announces how many molecules match", async () => {
        await open()
        await advance(100)
        expect(screen.getByText("2 molecules match", { selector: ".announcer" })).toBeInTheDocument()
    })

    it("says when no molecule has that name", async () => {
        handler = () => json(nameBody([]))
        await open("zzzz")
        expect(screen.getByText("No molecule with that name. Names come from PubChem and curators; try a formula or SMILES.")).toBeVisible()
        expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
        expect(box()).toHaveAttribute("aria-expanded", "false")
    })

    it("moves with the arrow keys and tracks the highlighted option", async () => {
        await open()
        const [first, second] = screen.getAllByRole("option")
        expect(box()).not.toHaveAttribute("aria-activedescendant")
        fireEvent.keyDown(box(), { key: "ArrowDown" })
        expect(box()).toHaveAttribute("aria-activedescendant", first.id)
        expect(first).toHaveAttribute("aria-selected", "true")
        fireEvent.keyDown(box(), { key: "ArrowDown" })
        expect(box()).toHaveAttribute("aria-activedescendant", second.id)
        fireEvent.keyDown(box(), { key: "ArrowDown" })
        expect(box()).toHaveAttribute("aria-activedescendant", first.id)
        fireEvent.keyDown(box(), { key: "ArrowUp" })
        expect(box()).toHaveAttribute("aria-activedescendant", second.id)
    })

    it("opens the highlighted molecule on Enter", async () => {
        await open()
        fireEvent.keyDown(box(), { key: "ArrowDown" })
        fireEvent.keyDown(box(), { key: "ArrowDown" })
        fireEvent.keyDown(box(), { key: "Enter" })
        expect(screen.getByText(/landed on species/)).toBeInTheDocument()
        expect(nameCalls().every((call) => call.url.searchParams.get("match") === "prefix")).toBe(true)
    })

    it("opens a molecule when its option is clicked", async () => {
        await open()
        fireEvent.click(screen.getAllByRole("option")[0])
        expect(screen.getByText(/landed on species/)).toBeInTheDocument()
    })

    it("closes on Escape and clears the highlight", async () => {
        await open()
        fireEvent.keyDown(box(), { key: "ArrowDown" })
        fireEvent.keyDown(box(), { key: "Escape" })
        expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
        expect(box()).toHaveAttribute("aria-expanded", "false")
        expect(box()).not.toHaveAttribute("aria-activedescendant")
    })
})

describe("submitting a name", () => {
    function submit() { fireEvent.submit(box().closest("form") as HTMLFormElement) }

    it("runs an exact name search and goes straight to a single hit", async () => {
        handler = () => json(nameBody([water]))
        page()
        typeText("Water"); submit()
        await advance(10)
        expect(nameCalls().map((call) => call.url.searchParams.get("match"))).toEqual(["exact"])
        expect(screen.getByText(/landed on species/)).toBeInTheDocument()
    })

    it("lists several hits with the source note and the order statement", async () => {
        page()
        typeText("Water"); submit()
        await advance(10)
        expect(screen.getByRole("heading", { name: "Molecules with that name" })).toBeVisible()
        expect(screen.getAllByRole("link")).toHaveLength(2)
        expect(screen.getByText("from PubChem")).toBeVisible()
        expect(screen.getByText("Alphabetical, not a ranking")).toBeVisible()
    })

    it("says so when no molecule has that name", async () => {
        handler = () => json(nameBody([]))
        page()
        typeText("Unobtainium"); submit()
        await advance(10)
        expect(screen.getByText(/^No molecule with that name\./)).toBeVisible()
    })

    it("does not touch name search for a SMILES, which still goes to the exact search", async () => {
        handler = () => json({ records: [] })
        page()
        typeText("CC(=O)O"); submit()
        await advance(10)
        expect(nameCalls()).toHaveLength(0)
        expect(calls[0].url.pathname).toMatch(/species\/structure-search$/)
    })

    it("does not send a request while a back-off is running", async () => {
        handler = () => json({ detail: "slow", code: "rate_limited" }, 429, { "Retry-After": "20" })
        page()
        typeText("Water"); submit()
        await advance(10)
        expect(screen.getByText("Too many searches; try again in 20 seconds.")).toBeVisible()
        submit()
        await advance(10)
        expect(nameCalls()).toHaveLength(1)
    })
})
