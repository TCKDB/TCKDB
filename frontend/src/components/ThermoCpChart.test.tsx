import { describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach } from "vitest"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { ThermoRecord } from "../api/thermoApi"
import { ThermoCpChart } from "./ThermoCpChart"

afterEach(() => cleanup())

function conformer(ref: string, label: string): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: ref, label },
        observations_summary: { total: 1 },
        evidence_summary: {
            calculation_count: 1, optimization_chain_count: 1, geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 1, sp: 1 }, levels_of_theory: {},
        },
        observations: [], calculations: [], geometries: [],
    } as unknown as ConformerProjection
}

const conformers = [conformer("cg_one", "conformer_1"), conformer("cg_two", "conformer_2")]

// Two records, deliberately DIFFERENT in every field this file tests:
// different conformer link, different NASA coefficients, different
// measured points -- a fixture where "render every series from records[0]"
// is directly observable on the second series, not hidden behind two
// identical rows.
function recordAlpha(): ThermoRecord {
    return {
        thermo_ref: "thm_alpha", scientific_origin: "computed", model_kind: "nasa",
        review: { status: "not_reviewed" }, supersession: null,
        h298_kj_mol: null, s298_j_mol_k: null, h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
        nasa: {
            t_low: 100, t_mid: 500, t_high: 1000,
            low_temperature_coefficients: [1, 0, 0, 0, 0, 0, 0],
            high_temperature_coefficients: [1.4, 0, 0, 0, 0, 0, 0],
        },
        nasa9: null, wilhoit: null,
        points: [
            { temperature_k: 150, cp_j_mol_k: 9 },
            { temperature_k: 900, cp_j_mol_k: 12 },
        ],
        temperature_coverage: null,
        evidence_completeness: { score: 0, max: 8, checklist: {} },
        provenance: { conformer_group_ref: "cg_one" },
        group_additivity: null,
    } as unknown as ThermoRecord
}

function recordBeta(): ThermoRecord {
    return {
        thermo_ref: "thm_beta", scientific_origin: "computed", model_kind: "nasa",
        review: { status: "not_reviewed" }, supersession: null,
        h298_kj_mol: null, s298_j_mol_k: null, h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
        nasa: {
            t_low: 100, t_mid: 500, t_high: 1000,
            low_temperature_coefficients: [3, 0, 0, 0, 0, 0, 0],
            high_temperature_coefficients: [3.6, 0, 0, 0, 0, 0, 0],
        },
        nasa9: null, wilhoit: null,
        points: [
            { temperature_k: 200, cp_j_mol_k: 55 },
            { temperature_k: 950, cp_j_mol_k: 88 },
        ],
        temperature_coverage: null,
        evidence_completeness: { score: 0, max: 8, checklist: {} },
        provenance: { conformer_group_ref: "cg_two" },
        group_additivity: null,
    } as unknown as ThermoRecord
}

function fitOnlyRecord(): ThermoRecord {
    return { ...recordAlpha(), thermo_ref: "thm_fit_only", points: null, provenance: { conformer_group_ref: null } } as ThermoRecord
}

function pointsOnlyRecord(): ThermoRecord {
    return { ...recordAlpha(), thermo_ref: "thm_points_only", nasa: null, provenance: { conformer_group_ref: null } } as ThermoRecord
}

function neitherRecord(): ThermoRecord {
    return { ...recordAlpha(), thermo_ref: "thm_neither", nasa: null, points: null, provenance: { conformer_group_ref: null } } as ThermoRecord
}

// A "neither" record (no fit, no points -- plots nothing) linked to a given
// conformer group, so two of these can be built on DIFFERENT groups. This is
// the live-bug shape from the review's own repro:
// [fit-on-cfg_1, empty-on-cfg_1, empty-on-cfg_2].
function neitherOnGroup(ref: string, groupRef: string): ThermoRecord {
    return {
        ...recordAlpha(),
        thermo_ref: ref,
        nasa: null,
        points: null,
        provenance: { conformer_group_ref: groupRef },
    } as ThermoRecord
}

// Same conformer link, same nasa fit, same points as `recordAlpha()` -- only
// the ref differs. This is the live-page shape (`spe_5nr24y2ssxokx…`: seven
// `thm_` records, byte-identical NASA-7 coefficients and points) the
// collapsing tests below exercise.
function identicalToAlpha(ref: string): ThermoRecord {
    return { ...recordAlpha(), thermo_ref: ref } as ThermoRecord
}

// Shares `recordAlpha()`'s conformer link (so the same base legend label)
// but carries a GENUINELY different fit -- the "reprocessed later" case
// `groupLegendLabel` disambiguates by ref rather than collapsing.
function reprocessedAlpha(): ThermoRecord {
    return {
        ...recordAlpha(),
        thermo_ref: "thm_alpha_v2",
        nasa: {
            t_low: 100, t_mid: 500, t_high: 1000,
            low_temperature_coefficients: [2, 0, 0, 0, 0, 0, 0],
            high_temperature_coefficients: [2.4, 0, 0, 0, 0, 0, 0],
        },
    } as ThermoRecord
}

function renderChart(records: ThermoRecord[], selectedConformerGroupRef: string | null = null) {
    return render(
        <ThermoCpChart records={records} conformers={conformers} selectedConformerGroupRef={selectedConformerGroupRef} />,
    )
}

describe("ThermoCpChart — multiple conformers overlay as separate series", () => {
    it("renders one legend entry and one plotted series group per record, labelled by its own conformer", () => {
        renderChart([recordAlpha(), recordBeta()])
        expect(screen.getByTestId("legend-thm_alpha")).toHaveTextContent("Conformer Group 1")
        expect(screen.getByTestId("legend-thm_beta")).toHaveTextContent("Conformer Group 2")
        expect(screen.getByTestId("series-thm_alpha")).toBeInTheDocument()
        expect(screen.getByTestId("series-thm_beta")).toBeInTheDocument()

        // The second record's own measured markers, bound by their
        // data-testid to their OWN thermo_ref -- this fails if a
        // records[0]-only bug renders thm_beta's series using thm_alpha's
        // two points at 150K/900K instead of thm_beta's own 200K/950K.
        expect(screen.getByTestId("measured-point-thm_beta-0")).toHaveAttribute("cx")
        expect(screen.getByTestId("measured-point-thm_beta-1")).toHaveAttribute("cx")
        expect(screen.queryByTestId("measured-point-thm_beta-2")).not.toBeInTheDocument()
    })

    it("highlights only the series tracing to the selected conformer, never a default first-record highlight", () => {
        renderChart([recordAlpha(), recordBeta()], "cg_two")
        expect(screen.getByTestId("series-thm_alpha")).toHaveAttribute("data-selected", "false")
        expect(screen.getByTestId("series-thm_beta")).toHaveAttribute("data-selected", "true")
        expect(screen.getByTestId("legend-thm_beta")).toHaveClass("cp-chart-legend-item--selected")
        expect(screen.getByTestId("legend-thm_alpha")).not.toHaveClass("cp-chart-legend-item--selected")

        // Distinguished visually too: the selected series' fitted line is
        // drawn wider than the unselected one's.
        const selectedStroke = screen.getByTestId("fit-low-thm_beta").getAttribute("stroke-width")
        const unselectedStroke = screen.getByTestId("fit-low-thm_alpha").getAttribute("stroke-width")
        expect(Number(selectedStroke)).toBeGreaterThan(Number(unselectedStroke))

        // No selection at all -> nothing carries the selected flag.
        cleanup()
        renderChart([recordAlpha(), recordBeta()], null)
        expect(screen.getByTestId("series-thm_alpha")).toHaveAttribute("data-selected", "false")
        expect(screen.getByTestId("series-thm_beta")).toHaveAttribute("data-selected", "false")
    })

    it("never combines or averages the two series into one line — each keeps its own fitted curve endpoint", () => {
        renderChart([recordAlpha(), recordBeta()])
        // thm_alpha's high branch ends at Cp = 1.4 * R; thm_beta's ends at
        // Cp = 3.6 * R -- if the two were ever averaged or merged, both
        // series would report the SAME endpoint value.
        const alphaHigh = screen.getByTestId("fit-high-thm_alpha").getAttribute("points")!
        const betaHigh = screen.getByTestId("fit-high-thm_beta").getAttribute("points")!
        expect(alphaHigh).not.toBe(betaHigh)
    })
})

describe("ThermoCpChart — POINTS/FIT/BOTH toggle", () => {
    it("shows both markers and curves by default, hides curves under Points, hides markers under Fit", () => {
        renderChart([recordAlpha()])
        expect(screen.getByTestId("fit-low-thm_alpha")).toBeInTheDocument()
        expect(screen.getByTestId("measured-point-thm_alpha-0")).toBeInTheDocument()

        fireEvent.click(screen.getByRole("button", { name: "Points" }))
        expect(screen.queryByTestId("fit-low-thm_alpha")).not.toBeInTheDocument()
        expect(screen.getByTestId("measured-point-thm_alpha-0")).toBeInTheDocument()

        fireEvent.click(screen.getByRole("button", { name: "Fit" }))
        expect(screen.getByTestId("fit-low-thm_alpha")).toBeInTheDocument()
        expect(screen.queryByTestId("measured-point-thm_alpha-0")).not.toBeInTheDocument()

        fireEvent.click(screen.getByRole("button", { name: "Both" }))
        expect(screen.getByTestId("fit-low-thm_alpha")).toBeInTheDocument()
        expect(screen.getByTestId("measured-point-thm_alpha-0")).toBeInTheDocument()
    })
})

describe("ThermoCpChart — unit toggle", () => {
    it("converts displayed Cp for display only, and the axis label names the active unit", () => {
        renderChart([recordAlpha()])
        expect(screen.getByRole("img", { name: /J\/mol·K/ })).toBeInTheDocument()
        const originalCy = screen.getByTestId("measured-point-thm_alpha-0").getAttribute("cy")

        fireEvent.click(screen.getByRole("button", { name: "cal/mol·K" }))
        expect(screen.getByRole("img", { name: /cal\/mol·K/ })).toBeInTheDocument()
        const convertedCy = screen.getByTestId("measured-point-thm_alpha-0").getAttribute("cy")
        expect(convertedCy).not.toBe(originalCy)

        // Round trip: switching back to J/mol/K reproduces the exact
        // original pixel position -- the stored value was never re-derived
        // from the converted display value.
        fireEvent.click(screen.getByRole("button", { name: "J/mol·K" }))
        expect(screen.getByTestId("measured-point-thm_alpha-0")).toHaveAttribute("cy", originalCy!)
    })
})

describe("ThermoCpChart — the three honest-absence cases", () => {
    it("a fit-only record: draws the curve and says plainly there are no measured points", () => {
        renderChart([fitOnlyRecord()])
        expect(screen.getByTestId("fit-low-thm_fit_only")).toBeInTheDocument()
        expect(screen.queryByTestId("measured-point-thm_fit_only-0")).not.toBeInTheDocument()
        expect(screen.getByText(/no evaluated points on file for this record/)).toBeInTheDocument()
    })

    it("a points-only record: draws markers, no curve", () => {
        renderChart([pointsOnlyRecord()])
        expect(screen.queryByTestId("fit-low-thm_points_only")).not.toBeInTheDocument()
        expect(screen.getByTestId("measured-point-thm_points_only-0")).toBeInTheDocument()
        expect(screen.getByText(/no usable NASA-7 fit on file for this record/)).toBeInTheDocument()
    })

    it("a record with neither, alongside one that has data: nothing is plotted for it, and it is named explicitly rather than silently dropped", () => {
        // Paired with a record that DOES have data so the chart still
        // renders panels at all -- a lone neither-record instead hits the
        // whole-entry "nothing to plot anywhere" case, covered separately
        // below.
        renderChart([recordAlpha(), neitherRecord()])
        expect(screen.getByTestId("series-thm_alpha")).toBeInTheDocument()
        // The series group still exists (it's still one of the entry's
        // deposited records) but has nothing drawn inside it -- no fit line,
        // no marker.
        expect(screen.getByTestId("series-thm_neither")).toBeEmptyDOMElement()
        expect(screen.queryByTestId("fit-low-thm_neither")).not.toBeInTheDocument()
        expect(screen.queryByTestId("measured-point-thm_neither-0")).not.toBeInTheDocument()
        expect(screen.getByText(/no evaluated points or usable NASA-7 fit on file for this record — nothing plotted/)).toBeInTheDocument()
    })
})

describe("ThermoCpChart — collapsing records that plot as the exact same line", () => {
    it("draws several byte-identical records as ONE line and names the count in the one legend chip, rather than N indistinguishable, identically-labelled chips", () => {
        renderChart([identicalToAlpha("thm_dup_1"), identicalToAlpha("thm_dup_2"), identicalToAlpha("thm_dup_3")])

        expect(screen.getByTestId("legend-thm_dup_1")).toHaveTextContent("Conformer Group 1 — 3 identical records")
        expect(screen.queryByTestId("legend-thm_dup_2")).not.toBeInTheDocument()
        expect(screen.queryByTestId("legend-thm_dup_3")).not.toBeInTheDocument()

        expect(screen.getByTestId("series-thm_dup_1")).toBeInTheDocument()
        expect(screen.queryByTestId("series-thm_dup_2")).not.toBeInTheDocument()
        expect(screen.queryByTestId("series-thm_dup_3")).not.toBeInTheDocument()

        // Every collapsed record's own ref is still named somewhere, not
        // silently dropped -- available on hover rather than asserted only
        // as a bare count.
        expect(screen.getByTestId("legend-thm_dup_1")).toHaveAttribute("title", "thm_dup_1, thm_dup_2, thm_dup_3")
    })

    it("never collapses two records that share a conformer link but carry a genuinely different fit -- and disambiguates their now-identical base label by ref", () => {
        renderChart([recordAlpha(), reprocessedAlpha()])

        // Both still draw as two separate lines.
        expect(screen.getByTestId("series-thm_alpha")).toBeInTheDocument()
        expect(screen.getByTestId("series-thm_alpha_v2")).toBeInTheDocument()
        expect(screen.getByTestId("fit-high-thm_alpha").getAttribute("points"))
            .not.toBe(screen.getByTestId("fit-high-thm_alpha_v2").getAttribute("points"))

        // Both trace to "Conformer Group 1" -- disambiguated by thermo_ref
        // rather than left as two chips reading the same label, distinguished
        // only by dot colour.
        expect(screen.getByTestId("legend-thm_alpha")).toHaveTextContent("Conformer Group 1 (thm_alpha)")
        expect(screen.getByTestId("legend-thm_alpha_v2")).toHaveTextContent("Conformer Group 1 (thm_alpha_v2)")
    })

    it("never collapses two records that both plot nothing, when they trace to DIFFERENT conformer groups -- the blocking review finding (repro shape: one plottable record plus one empty record per group)", () => {
        // Mirrors the review's own repro: [fit-on-cfg_1, empty-on-cfg_1, empty-on-cfg_2].
        renderChart([recordAlpha(), neitherOnGroup("thm_empty_1", "cg_one"), neitherOnGroup("thm_empty_2", "cg_two")])

        // Both empty records get their OWN chip -- neither disappears, and
        // they are never merged into a false "2 identical records" claim.
        expect(screen.getByTestId("legend-thm_empty_1")).toBeInTheDocument()
        expect(screen.getByTestId("legend-thm_empty_2")).toBeInTheDocument()
        expect(screen.queryByText(/identical records/)).not.toBeInTheDocument()

        // thm_alpha and thm_empty_1 share a conformer (cg_one) and therefore
        // a base label -- a genuine collision between two DIFFERENT records,
        // correctly disambiguated by ref.
        expect(screen.getByTestId("legend-thm_alpha")).toHaveTextContent("Conformer Group 1 (thm_alpha)")
        expect(screen.getByTestId("legend-thm_empty_1")).toHaveTextContent("Conformer Group 1 (thm_empty_1)")
        // thm_empty_2 (cg_two) has no colliding label -- plain.
        expect(screen.getByTestId("legend-thm_empty_2")).toHaveTextContent("Conformer Group 2")

        // Each empty record gets its OWN absence note -- never merged into
        // one shared note under the first group's label. (The note itself
        // uses the series' own base label, not the legend's disambiguated
        // form, so both read "Conformer Group N: ..." here.)
        const absenceNotes = screen.getAllByText(/no evaluated points or usable NASA-7 fit/)
        expect(absenceNotes).toHaveLength(2)
        expect(absenceNotes.some((el) => el.textContent?.startsWith("Conformer Group 1:"))).toBe(true)
        expect(absenceNotes.some((el) => el.textContent?.startsWith("Conformer Group 2:"))).toBe(true)
    })

    it("names every collapsed member's own ref in accessible text content, not only in a hover-only title attribute", () => {
        renderChart([identicalToAlpha("thm_dup_1"), identicalToAlpha("thm_dup_2"), identicalToAlpha("thm_dup_3")])

        // title= remains, for pointer users hovering the chip.
        expect(screen.getByTestId("legend-thm_dup_1")).toHaveAttribute("title", "thm_dup_1, thm_dup_2, thm_dup_3")
        // The same refs are also reachable as real text content -- not
        // dependent on a mouse hover, so keyboard and screen-reader users
        // get the same information pointer users do.
        expect(screen.getByTestId("legend-thm_dup_1").textContent).toMatch(/thm_dup_1.*thm_dup_2.*thm_dup_3/)
    })
})

describe("ThermoCpChart — 'evaluated points', never 'measured', since every record here is computed", () => {
    it("names the toggle Points and the intro sentence 'evaluated points', not 'measured'", () => {
        renderChart([recordAlpha()])
        expect(screen.getByRole("button", { name: "Points" })).toBeInTheDocument()
        expect(screen.queryByRole("button", { name: "Measured" })).not.toBeInTheDocument()
        expect(screen.getByText(/own evaluated points and NASA-7 fit/)).toBeInTheDocument()
    })
})

describe("ThermoCpChart — the temperature axis never goes negative, and both axes tick at round numbers", () => {
    it("clamps the x domain at 0 K and ticks both axes at nice round numbers, for a record whose own padded domain used to go negative", () => {
        // `recordAlpha()`'s nasa block runs t_low=100..t_high=1000; the OLD
        // 4%-padded domain would have started below 100 already, and a
        // record starting near 0 K (this archive's live data commonly
        // states t_low around 10 K) used to pad straight through zero into
        // negative kelvin -- the review finding this test guards.
        renderChart([recordAlpha()])
        const chart = screen.getByRole("img", { name: /Heat capacity versus temperature/ })
        const tickTexts = Array.from(chart.querySelectorAll(".cp-chart-tick-label--x")).map((el) => el.textContent)

        expect(tickTexts.length).toBeGreaterThan(0)
        for (const text of tickTexts) {
            expect(text).not.toMatch(/^-/)
        }
        // Every x tick is a whole, round number (nice-step ticks never
        // produce a bare-eval float like "698" or "-110").
        for (const text of tickTexts) {
            expect(Number(text)).not.toBeNaN()
            expect(Number.isInteger(Number(text))).toBe(true)
        }
        // The axis starts at 0 -- domain clamped, not padded outward.
        expect(tickTexts).toContain("0")
    })
})

// The residual (measured-minus-fitted) panel that used to render beneath
// the Cp-vs-T panel was REMOVED -- checked against a real record
// (`thm_5dzg66kvcuslgw6swkuc7gduiu`), this archive's stored "measured"
// points reproduce the NASA-7 fit to four decimal places everywhere
// checked (0.000% of Cp), because they were evidently evaluated FROM the
// fit in the first place -- a residual plot compared a curve with itself
// and was flat by construction. See `ThermoCpChart.tsx`'s own module
// comment for the full measurement. This positively asserts the Cp-vs-T
// chart itself is still here, and that no residual-panel DOM survives.
describe("ThermoCpChart — the residual panel is gone; the Cp-vs-T chart is not", () => {
    it("still renders the Cp-vs-T chart (axes, fitted curve, measured markers) with no residual panel alongside it", () => {
        renderChart([recordAlpha(), recordBeta()])
        expect(screen.getByRole("img", { name: /Heat capacity versus temperature/ })).toBeVisible()
        expect(screen.getByTestId("fit-low-thm_alpha")).toBeInTheDocument()
        expect(screen.getByTestId("measured-point-thm_alpha-0")).toBeInTheDocument()

        expect(screen.queryByTestId("residual-zero-line")).not.toBeInTheDocument()
        expect(screen.queryByTestId("residuals-thm_alpha")).not.toBeInTheDocument()
        expect(screen.queryByText(/Residual \(% of fit\)/)).not.toBeInTheDocument()
        expect(document.querySelector(".cp-chart-panel--residual")).toBeNull()
    })
})

describe("ThermoCpChart — no data anywhere", () => {
    it("says plainly that nothing can be plotted rather than rendering empty panels", () => {
        renderChart([neitherRecord()])
        expect(within(screen.getByRole("heading", { name: "Heat capacity vs. temperature" }).closest("section")!)
            .queryByRole("img")).not.toBeInTheDocument()
    })
})
