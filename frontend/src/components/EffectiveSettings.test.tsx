import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { EffectiveSetting, RoleCalculationSettings } from "../api/effectiveSettingsSchema"
import { EffectiveSettingsSummary, SettingsBehindValue } from "./EffectiveSettings"
import { expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"

afterEach(cleanup)

const grid = (over: Partial<EffectiveSetting>): EffectiveSetting => ({ setting: "grid", state: "not_stated", ...over })
const scf = (over: Partial<EffectiveSetting>): EffectiveSetting => ({ setting: "scf_convergence", state: "not_stated", ...over })

const BLOCK: RoleCalculationSettings[] = [
    {
        role: "geometry",
        calculation_ref: "calc_geometry000000000000000000",
        calculation_type: "opt",
        settings: [
            grid({ state: "reported", reported: [{ key: "grid.angular_max", value: "302" }] }),
            scf({ state: "assumed_default", assumed_value: "tight" }),
        ],
    },
    {
        role: "frequency",
        calculation_ref: "calc_frequency000000000000000000",
        calculation_type: "freq",
        settings: [
            grid({ state: "requested", requested_value: "ultrafine", requested_from: "input" }),
            scf({ state: "requested", requested_value: null, requested_from: "typed", conflicting: true }),
        ],
    },
    { role: "energy", calculation_ref: null, absent_reason: "not_linked", settings: [] },
]

function rowFor(level: string) {
    return screen.getByRole("row", { name: new RegExp(`^${level}`) })
}

describe("SettingsBehindValue", () => {
    it("renders one row per level, each with the calculation linked for that level", () => {
        render(<MemoryRouter><SettingsBehindValue block={BLOCK} recordRef="thr_x" /></MemoryRouter>)
        expect(screen.getByRole("heading", { name: "Settings behind this value" })).toBeInTheDocument()
        const rows = screen.getAllByRole("row").slice(1)
        expect(rows.map((row) => row.getAttribute("data-level-role"))).toEqual(["geometry", "frequency", "energy"])
        // The calculation in each row is the one the server put under that role (a swapped mapping would swap these).
        expect(within(rowFor("Geometry")).getByRole("link")).toHaveAttribute("href", "/calculations/calc_geometry000000000000000000")
        expect(within(rowFor("Frequencies")).getByRole("link")).toHaveAttribute("href", "/calculations/calc_frequency000000000000000000")
        expect(within(rowFor("Energy")).queryByRole("link")).toBeNull()
    })

    it("labels every state in words and marks an assumed value as assumed", () => {
        render(<MemoryRouter><SettingsBehindValue block={BLOCK} recordRef="thr_x" /></MemoryRouter>)
        const geometry = rowFor("Geometry")
        expect(within(geometry).getByText("Reported by the program")).toHaveAttribute("data-settings-state", "reported")
        expect(within(geometry).getByText("largest angular grid 302")).toBeInTheDocument()
        const assumed = within(geometry).getByText("Assumed program default")
        expect(assumed).toHaveAttribute("data-settings-state", "assumed")
        expect(assumed.className).toContain("settings-state--assumed")
        // The value itself carries the assumed styling too: an assumed value is never drawn like an observation.
        expect(within(geometry).getByText("tight").className).toContain("settings-value--assumed")

        const frequency = rowFor("Frequencies")
        expect(within(frequency).getByText("ultrafine").className).not.toContain("assumed")
        expect(within(frequency).getByText("input file")).toBeInTheDocument()
        expect(within(frequency).getByText("Conflicting values")).toBeInTheDocument()
    })

    it("says a level has no linked calculation instead of filling it", () => {
        render(<MemoryRouter><SettingsBehindValue block={BLOCK} recordRef="thr_x" /></MemoryRouter>)
        expect(within(rowFor("Energy")).getByText("No calculation linked")).toBeInTheDocument()
    })

    it("lists a level the server left out (its calculations are hidden) as not shown, never filled", () => {
        render(<MemoryRouter><SettingsBehindValue block={[BLOCK[0]]} recordRef="thr_x" /></MemoryRouter>)
        expect(screen.getAllByRole("row").slice(1).map((row) => row.getAttribute("data-level-role"))).toEqual(["geometry", "frequency", "energy"])
        expect(within(rowFor("Frequencies")).getByText(/not shown/)).toBeInTheDocument()
        expect(within(rowFor("Frequencies")).queryByRole("link")).toBeNull()
    })

    it("renders nothing when the record carries no block", () => {
        const { container } = render(<MemoryRouter><SettingsBehindValue block={null} recordRef="thr_x" /></MemoryRouter>)
        expect(container).toBeEmptyDOMElement()
    })

    it("prints no depositor-typed text, whichever field it was planted in", () => {
        const planted = plantSentinels(BLOCK, ["requested_value", "assumed_value", "key", "value", "absent_reason"])
        expectPlanted(planted)
        render(<MemoryRouter><SettingsBehindValue block={planted.value} recordRef="thr_x" /></MemoryRouter>)
        expectNoDepositorText()
        // The words that stand in for the typed values still render.
        expect(screen.getAllByText("recorded").length).toBeGreaterThan(0)
    })
})

describe("EffectiveSettingsSummary", () => {
    it("shows each state label for grid and SCF, with a conflict marker", () => {
        const settings = [
            grid({ state: "assumed_default_no_output_log", assumed_value: "defgrid2" }),
            scf({ state: "requested", requested_value: null, requested_from: "output_log", conflicting: true }),
        ]
        render(<EffectiveSettingsSummary settings={settings} />)
        const gridRow = screen.getByRole("row", { name: /DFT grid/ })
        expect(within(gridRow).getByText("Assumed program default (no output log)")).toHaveAttribute("data-settings-state", "assumed")
        expect(within(gridRow).getByText("defgrid2")).toBeInTheDocument()
        const scfRow = screen.getByRole("row", { name: /SCF convergence/ })
        expect(within(scfRow).getByText("Requested")).toBeInTheDocument()
        expect(within(scfRow).getByText("echoed in the output log")).toBeInTheDocument()
        expect(within(scfRow).getByText("Conflicting values")).toBeInTheDocument()
        expect(screen.getByText(/never stored/)).toBeInTheDocument()
    })

    it("shows not stated plainly and nothing when the section is absent", () => {
        const { container, rerender } = render(<EffectiveSettingsSummary settings={[grid({}), scf({})]} />)
        expect(screen.getAllByText("Not stated")).toHaveLength(2)
        rerender(<EffectiveSettingsSummary settings={null} />)
        expect(container).toBeEmptyDOMElement()
    })
})
