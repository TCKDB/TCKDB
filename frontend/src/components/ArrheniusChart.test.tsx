import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import type { ReactionKineticsRecord } from "../api/reactionEntryApi"
import {
    ARRHENIUS_CHART_HEIGHT,
    ARRHENIUS_CHART_MARGIN,
    ARRHENIUS_CHART_WIDTH,
    buildArrheniusChartData,
    panelLog10KDomain,
    panelTemperatureDomain,
} from "../domain/arrheniusChartLayout"
import { linearScale } from "../domain/chartScale"
import { ArrheniusChart } from "./ArrheniusChart"

afterEach(() => cleanup())

// The live pinned record (`kin_spkzatwjlvmmnja3i5im4fl7hq`) `kineticsTable.test.ts`
// and `arrheniusChartLayout.test.ts` both hand-compute against: A=3025.44
// cm³ mol⁻¹ s⁻¹, n=3.11242, Ea=39.9711 kJ/mol, 300-3000 K.
function bimolecularRecord(overrides: Record<string, unknown> = {}): ReactionKineticsRecord {
    return {
        kinetics_ref: "kin_spkzatwjlvmmnja3i5im4fl7hq",
        scientific_origin: "computed",
        model_kind: "modified_arrhenius",
        review: { status: "not_reviewed" },
        parameters: { A: 3025.44, A_units: "cm3_mol_s", n: 3.11242, Ea_kj_mol: 39.9711 },
        multi_arrhenius: null,
        tunneling_model: null,
        is_third_body: false,
        plog_entries: null,
        chebyshev: null,
        falloff: null,
        uncertainty: {},
        temperature_coverage: { record_min_k: 300, record_max_k: 3000 },
        evidence_completeness: { score: 0, max: 9, checklist: {} },
        levels: null,
        provenance: {},
        ...overrides,
    } as unknown as ReactionKineticsRecord
}

// A unimolecular record in a DIFFERENT A_units, per §8's live route
// (`rxe_snamm2m4daoyiw6ljc3cvmfabm`, A = 9444750000 s⁻¹) -- used for the
// per-unit panel split fixture.
function unimolecularRecord(overrides: Record<string, unknown> = {}): ReactionKineticsRecord {
    return bimolecularRecord({
        kinetics_ref: "kin_unimolecular_one",
        parameters: { A: 9444750000, A_units: "per_s", n: 0, Ea_kj_mol: 12.5 },
        temperature_coverage: { record_min_k: 300, record_max_k: 2500 },
        ...overrides,
    })
}

function plogRecord(overrides: Record<string, unknown> = {}): ReactionKineticsRecord {
    return bimolecularRecord({
        kinetics_ref: "kin_plog_record",
        model_kind: "plog",
        plog_entries: [{ pressure_bar: 1, A: 1, n: 0, Ea_kj_mol: 0 }],
        ...overrides,
    })
}

describe("ArrheniusChart -- a single plottable record", () => {
    it("renders one panel, one legend chip carrying the kin_ ref, and the k(T) table beneath it", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)

        expect(screen.getByRole("img", { name: /Arrhenius plot/ })).toBeInTheDocument()
        expect(screen.getByTestId("arrhenius-legend-kin_spkzatwjlvmmnja3i5im4fl7hq")).toHaveTextContent("kin_spkzatwjlvmmnja3i5im4fl7hq")
        expect(screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")).toBeInTheDocument()

        // The table is the accessible text equivalent, directly beneath.
        const table = document.querySelector(".kinetics-k-table")
        expect(table).not.toBeNull()
        expect(within(table!.closest("details")!).getByText(/k\(T\) table/)).toBeInTheDocument()
    })

    it("the aria-label names the unit and counts the plotted records", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const svg = screen.getByRole("img", { name: /Arrhenius plot/ })
        expect(svg.getAttribute("aria-label")).toMatch(/cm³ mol⁻¹ s⁻¹/)
        expect(svg.getAttribute("aria-label")).toMatch(/1 record/)
    })
})

describe("ArrheniusChart -- per-unit panel split on a mixed fixture", () => {
    it("a page mixing per_s and cm3_mol_s records renders two panels (two separate SVGs)", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord(), unimolecularRecord()]} />)
        const svgs = screen.getAllByRole("img", { name: /Arrhenius plot/ })
        expect(svgs).toHaveLength(2)

        const labels = svgs.map((svg) => svg.getAttribute("aria-label"))
        expect(labels.some((label) => label?.includes("cm³ mol⁻¹ s⁻¹"))).toBe(true)
        expect(labels.some((label) => label?.includes("s⁻¹") && !label.includes("cm³"))).toBe(true)

        expect(screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")).toBeInTheDocument()
        expect(screen.getByTestId("arrhenius-line-kin_unimolecular_one")).toBeInTheDocument()
    })
})

describe("ArrheniusChart -- PLOG/Chebyshev/falloff/third-body exclusion", () => {
    it("a PLOG record is listed with its own ref and a reason naming PLOG, never plotted as a line", () => {
        render(<ArrheniusChart kinetics={[plogRecord()]} />)
        expect(screen.queryByRole("img", { name: /Arrhenius plot/ })).not.toBeInTheDocument()
        expect(screen.queryByTestId("arrhenius-line-kin_plog_record")).not.toBeInTheDocument()

        const note = screen.getByText(/is not plotted/)
        expect(note.textContent).toContain("kin_plog_record")
        expect(note.textContent).toMatch(/PLOG/)
    })

    it("a Chebyshev record's note names Chebyshev; a falloff record's note names falloff; a third-body record's note names third-body", () => {
        render(
            <ArrheniusChart
                kinetics={[
                    bimolecularRecord({ kinetics_ref: "kin_cheb", model_kind: "chebyshev", chebyshev: { coefficients: [[1]] } }),
                    bimolecularRecord({ kinetics_ref: "kin_falloff", model_kind: "falloff", falloff: { kind: "troe" } }),
                    bimolecularRecord({ kinetics_ref: "kin_third_body", is_third_body: true }),
                ]}
            />,
        )
        const notes = screen.getAllByText(/is not plotted/).map((el) => el.textContent ?? "")
        expect(notes.some((text) => text.includes("kin_cheb") && /Chebyshev/.test(text))).toBe(true)
        expect(notes.some((text) => text.includes("kin_falloff") && /falloff/.test(text))).toBe(true)
        expect(notes.some((text) => text.includes("kin_third_body") && /third-body/.test(text))).toBe(true)
    })

    it("a mix of one plottable and one excluded record: the plottable one still gets a panel, the excluded one is only listed", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord(), plogRecord()]} />)
        expect(screen.getAllByRole("img", { name: /Arrhenius plot/ })).toHaveLength(1)
        expect(screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")).toBeInTheDocument()
        const excludedNote = screen.getByText(/is not plotted/)
        expect(excludedNote.textContent).toContain("kin_plog_record")
    })
})

describe("ArrheniusChart -- zero plottable records", () => {
    it("renders no SVG at all, and says plainly why", () => {
        render(<ArrheniusChart kinetics={[plogRecord()]} />)
        expect(screen.queryByRole("img")).not.toBeInTheDocument()
        expect(screen.getByText(/No rate coefficient among this reaction entry's kinetics records can be rendered/)).toBeInTheDocument()
        // The "why" -- the excluded note is still there, naming the reason.
        expect(screen.getByText(/is not plotted/)).toBeInTheDocument()
    })
})

describe("ArrheniusChart -- a sampled point's rendered pixel position matches the layout module's own mapping", () => {
    it("recomputes an interior point's x/y from arrheniusChartLayout's domain + chartScale's linearScale, independent of the component, and matches within 1px", () => {
        const records = [bimolecularRecord()]
        render(<ArrheniusChart kinetics={records} />)

        const polyline = screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")
        const renderedPoints = polyline.getAttribute("points")!.split(" ").map((pair) => pair.split(",").map(Number))
        expect(renderedPoints).toHaveLength(60)

        // Recompute the SAME layout independently, from the domain module only
        // -- never reaching into the component's own closure.
        const { panels } = buildArrheniusChartData(records)
        const panel = panels[0]
        const temperatureDomain = panelTemperatureDomain(panel)
        const kDomain = panelLog10KDomain(panel)
        const { top, right, bottom, left } = ARRHENIUS_CHART_MARGIN
        const plotWidth = ARRHENIUS_CHART_WIDTH - left - right
        const plotHeight = ARRHENIUS_CHART_HEIGHT - top - bottom
        const xScale = linearScale(temperatureDomain, [left, left + plotWidth])
        const yScale = linearScale(kDomain, [top + plotHeight, top])

        // Check an interior point (index 30), not just an endpoint -- an
        // implementation that only got the endpoints right (e.g. a domain
        // padded differently from the ticks') would still pass an endpoint-only
        // check.
        const seriesPoint = panel.series[0].points[30]
        const expectedX = xScale(seriesPoint.temperatureK)
        const expectedY = yScale(seriesPoint.log10k)
        const [renderedX, renderedY] = renderedPoints[30]

        expect(Math.abs(renderedX - expectedX)).toBeLessThanOrEqual(1)
        expect(Math.abs(renderedY - expectedY)).toBeLessThanOrEqual(1)
    })
})

describe("ArrheniusChart -- the table remains the text equivalent", () => {
    it("the k(T) table's first/last rows match the chart's own domain ends, in the same units", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const table = document.querySelector(".kinetics-k-table") as HTMLTableElement
        const rows = Array.from(table.querySelectorAll("tbody tr"))
        expect(rows[0].querySelector('[data-label="T (K)"]')?.textContent).toBe("300.00")
        expect(rows[rows.length - 1].querySelector('[data-label="T (K)"]')?.textContent).toBe("3000.00")
        expect(table.querySelector("caption")?.textContent).toMatch(/cm³ mol⁻¹ s⁻¹/)
    })
})
