import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import CompositeSchemePage from "./CompositeSchemePage"
import { resetAllRequestCaches } from "../api/requestCache"
import "../design-system.css"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
beforeEach(() => resetAllRequestCaches())
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const ENDPOINT = "/api/v1/scientific/composite-schemes/csch_x"

function page() {
    return render(
        <MemoryRouter initialEntries={["/methods/composite-schemes/csch_x"]}>
            <Routes>
                <Route path="/methods/composite-schemes/:schemeRef" element={<CompositeSchemePage />} />
            </Routes>
        </MemoryRouter>,
    )
}

const lot = (basis: string, core?: string) => ({
    level_of_theory_ref: `lot_${basis}${core ?? ""}`,
    method: "CCSD(T)",
    basis,
    display: `CCSD(T)/${basis}`,
    label: core ? `CCSD(T)/${basis} (core=${core})` : `CCSD(T)/${basis}`,
})

function core(overrides: Record<string, unknown> = {}) {
    return {
        composite_scheme_ref: "csch_x",
        kind: "extrapolation",
        name: "CBS[ref:CCSD(T)/cc-pVQZ + corr:CCSD(T)/cc-pV{T,Q}Z; inverse_power x=3; n=3,4]",
        definition_hash: "h",
        geometry_level_of_theory: null,
        frequency_level_of_theory: null,
        recipe_zpe_scale_factor: null,
        source_literature_ref: null,
        note: null,
        created_at: "2026-10-03T12:00:00",
        ...overrides,
    }
}

function record(body: Record<string, unknown>) {
    return { record: { bound_levels_of_theory: [], terms: [], linear_in_energies: null, ...body } }
}

async function load(body: Record<string, unknown>) {
    server.use(http.get(ENDPOINT, () => HttpResponse.json(record(body))))
    page()
    return screen.findByRole("heading", { level: 1, name: /recipe$/ })
}

const EXTRAPOLATION_TERMS = [
    {
        position: 0, operation: "value", energy_component: "reference", formula: null, exponent: null, linearity: "linear",
        inputs: [{ slot: "value", cardinal_number: null, coefficient: 1, level_of_theory: lot("cc-pVQZ") }],
    },
    {
        position: 1, operation: "extrapolation", energy_component: "correlation", formula: "inverse_power", exponent: 3, linearity: "linear",
        inputs: [
            { slot: "cardinal", cardinal_number: 3, coefficient: -27 / 37, level_of_theory: lot("cc-pVTZ") },
            { slot: "cardinal", cardinal_number: 4, coefficient: 64 / 37, level_of_theory: lot("cc-pVQZ") },
        ],
    },
]

describe("CompositeSchemePage", () => {
    it("titles a user-built recipe from its kind, never the long server label, in the heading and the breadcrumb", async () => {
        const h1 = await load({ composite_scheme: core(), terms: EXTRAPOLATION_TERMS, linear_in_energies: true })
        expect(h1).toHaveTextContent("Basis-set extrapolation recipe")
        expect(h1.textContent).not.toContain("CBS[")
        const crumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(crumb.querySelector("[aria-current='page']")?.textContent).toBe("Basis-set extrapolation recipe")
        // The full label stays on the page, beneath the title, in mono.
        const label = screen.getByText(/^CBS\[ref:CCSD\(T\)/)
        expect(label.tagName).toBe("CODE")
    })

    it("titles a named method by its own short name", async () => {
        const h1 = await load({ composite_scheme: core({ kind: "named_method", name: "CBS-QB3" }) })
        expect(h1).toHaveTextContent("CBS-QB3 recipe")
    })

    it("shows each coefficient and the sum, numbers terms from 1, and words the slots as the server defines them", async () => {
        await load({ composite_scheme: core(), terms: EXTRAPOLATION_TERMS, linear_in_energies: true })
        expect(screen.getByRole("heading", { level: 3, name: "Term 1" })).toBeInTheDocument()
        expect(screen.getByRole("heading", { level: 3, name: "Term 2" })).toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Term 0" })).not.toBeInTheDocument()
        const inputs = screen.getByRole("table", { name: "Inputs of term 2" })
        expect(within(inputs).getByText("-27/37 (-0.729730)")).toBeInTheDocument()
        expect(within(inputs).getByText("64/37 (1.729730)")).toBeInTheDocument()
        expect(screen.getByText("−27/37 E(TZ) + 64/37 E(QZ)")).toBeInTheDocument()
        expect(screen.getByText("The total is a fixed weighted sum of the input energies.")).toBeInTheDocument()
    })

    it("puts the coefficient column right after the input level", async () => {
        await load({ composite_scheme: core(), terms: EXTRAPOLATION_TERMS, linear_in_energies: true })
        const headers = within(screen.getByRole("table", { name: "Inputs of term 2" })).getAllByRole("columnheader").map((h) => h.textContent)
        expect(headers).toEqual(["Input level of theory", "Coefficient", "Slot", "Cardinal number"])
    })

    it("says a non-linear term has no fixed coefficients, and gives none", async () => {
        await load({
            composite_scheme: core(),
            linear_in_energies: false,
            terms: [{
                position: 0, operation: "extrapolation", energy_component: "total", formula: "exponential_three_point", exponent: null,
                linearity: "nonlinear",
                inputs: [2, 3, 4].map((n) => ({ slot: "cardinal", cardinal_number: n, coefficient: null, level_of_theory: lot(`cc-pV${n}Z`) })),
            }],
        })
        expect(screen.getByText("non-linear (three-point exponential), no fixed coefficients")).toBeInTheDocument()
        expect(screen.getAllByText("none, not a fixed weight")).toHaveLength(3)
        expect(screen.queryByText("As a sum")).not.toBeInTheDocument()
        expect(screen.getByText(/not a fixed weighted sum/)).toBeInTheDocument()
    })

    it("tells the all-electron and frozen-core sides of a core-valence difference apart, with high and low sides", async () => {
        await load({
            composite_scheme: core({ kind: "additive", name: "Additive[...]" }),
            linear_in_energies: true,
            terms: [{
                position: 0, operation: "difference", energy_component: "total", formula: null, exponent: null, linearity: "linear",
                inputs: [
                    { slot: "high", cardinal_number: null, coefficient: 1, level_of_theory: lot("cc-pCVTZ", "all_electron") },
                    { slot: "low", cardinal_number: null, coefficient: -1, level_of_theory: lot("cc-pCVTZ", "frozen_core") },
                ],
            }],
        })
        const sum = screen.getByText(/^E\(CCSD\(T\)\/cc-pCVTZ/)
        expect(sum.textContent).toBe("E(CCSD(T)/cc-pCVTZ (core=all_electron)) − E(CCSD(T)/cc-pCVTZ (core=frozen_core))")
        const inputs = screen.getByRole("table", { name: "Inputs of term 1" })
        expect(within(inputs).getByRole("link", { name: "CCSD(T)/cc-pCVTZ (core=all_electron)" })).toBeInTheDocument()
        expect(within(inputs).getByRole("link", { name: "CCSD(T)/cc-pCVTZ (core=frozen_core)" })).toBeInTheDocument()
        expect(within(inputs).getByText("high side")).toBeInTheDocument()
        expect(within(inputs).getByText("low side")).toBeInTheDocument()
        expect(within(inputs).queryByText(/larger basis|smaller basis/)).not.toBeInTheDocument()
    })

    it("says a recipe with no terms has none, once", async () => {
        await load({ composite_scheme: core({ kind: "named_method", name: "CBS-QB3" }) })
        expect(screen.getAllByText(/states no terms/)).toHaveLength(1)
    })
})
