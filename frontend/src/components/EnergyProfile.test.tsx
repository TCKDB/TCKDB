import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import "../design-system.css"
import { EnergyProfile } from "./EnergyProfile"
import { UNAVAILABLE_WORDS } from "../domain/energyProfile"
import { rankingWords, textOf } from "../test/rankingWords"

const REF = "rxe_prof1"
const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const software = (version: string | null) => ({ program: "Gaussian", version })
const lot = (ref: string, method: string) => ({ level_of_theory_ref: ref, method, basis: "def2tzvp" })
const DEFINITION = "Each molecule's energy at this level from this program. Electronic energy, no zero-point correction."

function point(calc: string, hartree: number, extra: Record<string, unknown> = {}) {
    return { calculation_ref: calc, energy_type: "single_point", electronic_energy_hartree: hartree, software: software("16"), review_status: "not_reviewed", convergence_unrecorded: false, ...extra }
}

function profile(tse: string, level: ReturnType<typeof lot>, program: string, kj: [number, number, number], extra: Record<string, unknown> = {}) {
    return {
        transition_state_entry_ref: tse,
        transition_state_entry_review_status: "not_reviewed",
        level_of_theory: level,
        program,
        energy_types: ["single_point"],
        versions: ["16"],
        energy_kind: "electronic",
        definition: DEFINITION,
        reactants: [{ species_entry_ref: "spe_a", count: 1, ...point("calc_ra", -40.1) }, { species_entry_ref: "spe_b", count: 1, ...point("calc_rb", -75.2) }],
        transition_state: { transition_state_entry_ref: tse, ...point("calc_ts", -115.2) },
        products: [{ species_entry_ref: "spe_c", count: 2, ...point("calc_pc", -57.6) }],
        energy_hartree: { reactants: -115.3, transition_state: -115.2, products: -115.2 },
        relative_kj_mol: { reactants: 0, transition_state: kj[1], products: kj[2] },
        ...extra,
    }
}

const FIRST = profile("tse_one", lot("lot_a", "wb97xd"), "Gaussian", [0, 84.31, -12.04])
const SECOND = profile("tse_one", lot("lot_b", "b3lyp"), "Orca", [0, 40.2, -5], {
    energy_types: ["single_point", "optimization"],
    versions: ["5", null],
    transition_state: { transition_state_entry_ref: "tse_one", ...point("calc_ts2", -115.25, { energy_type: "optimization", convergence_unrecorded: true, software: { program: "Orca", version: null } }) },
})
const THIRD = profile("tse_two", lot("lot_a", "wb97xd"), "Gaussian", [0, 70, 3])

function read(overrides: Record<string, unknown> = {}) {
    return { request: { hidden_by_default: null, hidden_by_default_by_scope: {} }, reaction_entry_ref: REF, profiles: [FIRST, SECOND, THIRD], unavailable: [], cited_by_kinetics: [], ...overrides }
}

function mount(payload: object) {
    server.use(http.get(`/api/v1/scientific/reaction-entries/${REF}/energy-profile`, () => HttpResponse.json(payload)))
    return render(
        <MemoryRouter>
            <EnergyProfile entryRef={REF} speciesLabels={new Map([["spe_a", "CH4"], ["spe_b", "OH"], ["spe_c", "H2O"]])} />
        </MemoryRouter>,
    )
}

async function loaded() {
    await screen.findByRole("group", { name: /Electronic energy, no zero-point correction/ })
}

describe("EnergyProfile", () => {
    it("draws the first profile in response order with its points, not one picked by energy", async () => {
        mount(read())
        await loaded()
        const figure = screen.getByRole("group", { name: /Electronic energy, no zero-point correction/ })
        expect(figure.tagName.toLowerCase()).toBe("svg")
        expect(figure).toHaveTextContent("+84.3 kJ/mol")
        expect(figure).toHaveTextContent("−12.0 kJ/mol")
        expect(figure.querySelectorAll("a")).toHaveLength(0)
        const picker = screen.getByLabelText("Profile") as HTMLSelectElement
        expect(picker.value).toBe("0")
        expect(screen.getByText(/The first is shown until you pick another/)).toBeInTheDocument()
        // The second profile has the smaller barrier; it is not the one shown.
        expect(figure).not.toHaveTextContent("+40.2")
    })

    it("lists every point as a link to its calculation, with kind of energy, program, review and the relative energy", async () => {
        mount(read())
        await loaded()
        const table = screen.getByRole("table", { name: /Every point of the selected profile/ })
        for (const calc of ["calc_ra", "calc_rb", "calc_ts", "calc_pc"]) {
            const link = within(table).getAllByRole("link").find((anchor) => anchor.getAttribute("href") === `/calculations/${calc}`)
            expect(link, calc).toBeTruthy()
        }
        expect(within(table).getByText("× 2")).toBeInTheDocument()
        expect(table).toHaveTextContent("Reactants, summed")
        expect(table).toHaveTextContent("+84.3 kJ/mol")
        expect(table).toHaveTextContent("Gaussian 16")
        expect(table).toHaveTextContent("not reviewed")
        expect(table).toHaveTextContent("single-point energy")
    })

    it("states electronic energy with no zero-point correction and the server's definition", async () => {
        mount(read())
        await loaded()
        expect(screen.getByText(DEFINITION)).toBeInTheDocument()
        expect(screen.getByRole("group", { name: /no zero-point correction/ })).toBeInTheDocument()
    })

    it("switches profile from the picker, marks unrecorded convergence and states mixed kinds and versions", async () => {
        const user = userEvent.setup()
        mount(read())
        await loaded()
        expect(screen.queryByText(/mixes kinds of energy/)).not.toBeInTheDocument()
        await user.selectOptions(screen.getByLabelText("Profile"), "1")
        const figure = screen.getByRole("group", { name: /Electronic energy, no zero-point correction/ })
        expect(figure).toHaveTextContent("+40.2 kJ/mol")
        expect(screen.getByText(/convergence not recorded/)).toBeInTheDocument()
        const mixed = screen.getByText(/mixes kinds of energy/)
        expect(mixed).toHaveTextContent("single-point energy and optimisation final energy")
        expect(mixed).toHaveTextContent("5, version not recorded")
    })

    it("names the transition-state entry in the picker only when there are several", async () => {
        mount(read())
        await loaded()
        const options = within(screen.getByLabelText("Profile")).getAllByRole("option").map((option) => option.textContent)
        expect(options[0]).toBe("tse_one, wb97xd/def2tzvp, Gaussian")
        expect(options[2]).toBe("tse_two, wb97xd/def2tzvp, Gaussian")
    })

    it("shows no picker for a single profile", async () => {
        mount(read({ profiles: [FIRST] }))
        await loaded()
        expect(screen.queryByLabelText("Profile")).not.toBeInTheDocument()
    })

    it("words every unavailable code, and shows an unknown code as code", async () => {
        const codes = [...Object.keys(UNAVAILABLE_WORDS), "something_new"]
        mount(read({
            profiles: [],
            unavailable: codes.map((code) => ({ transition_state_entry_ref: "tse_x", transition_state_entry_review_status: "approved", code, level_of_theory: lot("lot_a", "wb97xd"), program: "Gaussian" })),
        }))
        expect(await screen.findByText("No profile for these transition states")).toBeInTheDocument()
        const items = screen.getAllByRole("listitem")
        for (const code of Object.keys(UNAVAILABLE_WORDS)) {
            expect(items.some((item) => item.textContent?.includes(UNAVAILABLE_WORDS[code])), code).toBe(true)
            expect(document.body.textContent).not.toContain(code)
        }
        expect(screen.getByText("something_new").tagName.toLowerCase()).toBe("code")
        expect(screen.getByText("No complete energy profile is available for this reaction entry.")).toBeInTheDocument()
    })

    it("lists energies a rate record cites in a table of their own, never inside the profile table", async () => {
        mount(read({
            cited_by_kinetics: [{
                kinetics_ref: "kin_x",
                calculations: [
                    { role: "ts_energy", calculation_ref: "calc_cited", review_status: "approved", transition_state_entry_ref: "tse_one", energy_type: "single_point", electronic_energy_hartree: -115.19, software: software("16") },
                    { role: "reactant_energy", calculation_ref: "calc_nulls", review_status: "not_reviewed", species_entry_ref: "spe_a", electronic_energy_hartree: null },
                ],
            }],
        }))
        await loaded()
        const cited = screen.getByRole("table", { name: /Calculations cited by each rate record/ })
        expect(screen.getByText("Energies a rate record cites")).toBeInTheDocument()
        expect(cited).toHaveTextContent("kin_x")
        expect(cited).toHaveTextContent("Transition-state energy")
        expect(cited).toHaveTextContent("not recorded")
        const profileTable = screen.getByRole("table", { name: /Every point of the selected profile/ })
        expect(profileTable).not.toHaveTextContent("calc_cited")
        expect(profileTable).not.toHaveTextContent("kin_x")
    })

    it("says what the default view left out, by kind", async () => {
        mount(read({ request: { hidden_by_default: null, hidden_by_default_by_scope: { transition_state_entries: { rejected: 2 }, kinetics_records: { deprecated: 1 } } } }))
        await loaded()
        expect(screen.getByText("Not listed by default: 2 rejected transition-state entries.")).toBeInTheDocument()
        expect(screen.getByText("Not listed by default: 1 deprecated kinetics record.")).toBeInTheDocument()
    })

    it("says so when the read fails", async () => {
        server.use(http.get(`/api/v1/scientific/reaction-entries/${REF}/energy-profile`, () => HttpResponse.json({ detail: "x" }, { status: 500 })))
        render(<MemoryRouter><EnergyProfile entryRef={REF} speciesLabels={new Map()} /></MemoryRouter>)
        expect(await screen.findByText("The energy profile could not be loaded.")).toBeInTheDocument()
    })

    it("uses no ranking word of its own, and no literal double hyphen", async () => {
        mount(read({ unavailable: [{ transition_state_entry_ref: "tse_x", transition_state_entry_review_status: "approved", code: "no_ts_energy" }] }))
        await loaded()
        expect(rankingWords(textOf(document.body))).toEqual([])
        expect(textOf(document.body)).not.toContain("--")
    })

    it("reads with the keyboard: Tab reaches the picker, then the table's links", async () => {
        const user = userEvent.setup()
        mount(read())
        await loaded()
        const picker = screen.getByLabelText("Profile")
        await user.tab()
        expect(document.activeElement).toBe(picker)
        await user.selectOptions(picker, "2")
        expect(screen.getByRole("group", { name: /Electronic energy, no zero-point correction/ })).toHaveTextContent("+70.0 kJ/mol")
        const table = screen.getByRole("table", { name: /Every point of the selected profile/ })
        let reached = false
        for (let step = 0; step < 10 && !reached; step++) {
            await user.tab()
            reached = table.contains(document.activeElement) && document.activeElement?.tagName.toLowerCase() === "a"
        }
        expect(reached).toBe(true)
    })
})
