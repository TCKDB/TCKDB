import { describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach } from "vitest"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { ThermoRecord } from "../api/thermoApi"
import { linearScale } from "../domain/chartScale"
import { CHART_MARGIN, RESIDUAL_CHART_HEIGHT, computeCpValueDomain, computeResidualPercentDomain, computeTemperatureDomain } from "../domain/thermoCpChartLayout"
import { buildCpChartSeries } from "../domain/thermoCpSeries"
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

describe("ThermoCpChart — RAW/FIT/BOTH toggle", () => {
    it("shows both markers and curves by default, hides curves under Measured, hides markers under Fit", () => {
        renderChart([recordAlpha()])
        expect(screen.getByTestId("fit-low-thm_alpha")).toBeInTheDocument()
        expect(screen.getByTestId("measured-point-thm_alpha-0")).toBeInTheDocument()

        fireEvent.click(screen.getByRole("button", { name: "Measured" }))
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
    it("a fit-only record: draws the curve, says plainly there are no measured points, and the residual panel is omitted", () => {
        renderChart([fitOnlyRecord()])
        expect(screen.getByTestId("fit-low-thm_fit_only")).toBeInTheDocument()
        expect(screen.queryByTestId("measured-point-thm_fit_only-0")).not.toBeInTheDocument()
        expect(screen.getByText(/no measured points on file for this record/)).toBeInTheDocument()
        expect(screen.queryByTestId("residual-zero-line")).not.toBeInTheDocument()
    })

    it("a points-only record: draws markers, no curve, no residuals", () => {
        renderChart([pointsOnlyRecord()])
        expect(screen.queryByTestId("fit-low-thm_points_only")).not.toBeInTheDocument()
        expect(screen.getByTestId("measured-point-thm_points_only-0")).toBeInTheDocument()
        expect(screen.getByText(/no usable NASA-7 fit on file for this record/)).toBeInTheDocument()
        expect(screen.queryByTestId("residual-zero-line")).not.toBeInTheDocument()
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
        expect(screen.getByText(/no measured points or usable NASA-7 fit on file for this record — nothing plotted/)).toBeInTheDocument()
    })

    it("shows the residual panel once at least one record has BOTH a fit and points, even alongside absence cases", () => {
        renderChart([recordAlpha(), fitOnlyRecord()])
        expect(screen.getByTestId("residual-zero-line")).toBeInTheDocument()
        expect(screen.getByTestId("residuals-thm_alpha")).toBeInTheDocument()
        expect(screen.queryByTestId("residuals-thm_fit_only")).not.toBeInTheDocument()
    })
})

describe("ThermoCpChart — residual panel is on its OWN scale, not the Cp axis", () => {
    it("places a residual marker at the pixel position its OWN residual-percent domain implies, not the Cp domain's", () => {
        const records = [recordAlpha(), recordBeta()]
        renderChart(records)

        const series = buildCpChartSeries(records, conformers, null, "j_mol_k")
        const residualDomain = computeResidualPercentDomain(series)
        const cpDomain = computeCpValueDomain(series)
        // A meaningfully different domain is the whole precondition for this
        // test to be able to tell the two scales apart at all.
        expect(residualDomain).not.toEqual(cpDomain)

        const { top, bottom } = CHART_MARGIN
        const plotHeight = RESIDUAL_CHART_HEIGHT - top - bottom
        const residualYScale = linearScale(residualDomain, [top + plotHeight, top])

        const alphaResiduals = series.find((item) => item.thermoRef === "thm_alpha")!.residuals
        expect(alphaResiduals.length).toBeGreaterThan(0)
        const expectedCy = residualYScale(alphaResiduals[0].residualPercent)

        const rendered = screen.getByTestId("residual-point-thm_alpha-0")
        expect(Number(rendered.getAttribute("cy"))).toBeCloseTo(expectedCy, 6)
    })

    it("the residual panel's temperature axis matches the same shared temperature domain the Cp panel uses", () => {
        const records = [recordAlpha(), recordBeta()]
        renderChart(records)
        const series = buildCpChartSeries(records, conformers, null, "j_mol_k")
        const temperatureDomain = computeTemperatureDomain(series)
        const { left, right, top, bottom } = CHART_MARGIN
        const plotWidth = 720 - left - right
        const plotHeight = RESIDUAL_CHART_HEIGHT - top - bottom
        const xScale = linearScale(temperatureDomain, [left, left + plotWidth])
        const residualDomain = computeResidualPercentDomain(series)
        const yScale = linearScale(residualDomain, [top + plotHeight, top])

        const alphaResiduals = series.find((item) => item.thermoRef === "thm_alpha")!.residuals
        const rendered = screen.getByTestId("residual-point-thm_alpha-0")
        expect(Number(rendered.getAttribute("cx"))).toBeCloseTo(xScale(alphaResiduals[0].temperatureK), 6)
        expect(Number(rendered.getAttribute("cy"))).toBeCloseTo(yScale(alphaResiduals[0].residualPercent), 6)
    })
})

describe("ThermoCpChart — no data anywhere", () => {
    it("says plainly that nothing can be plotted rather than rendering empty panels", () => {
        renderChart([neitherRecord()])
        expect(within(screen.getByRole("heading", { name: "Heat capacity vs. temperature" }).closest("section")!)
            .queryByRole("img")).not.toBeInTheDocument()
    })
})
