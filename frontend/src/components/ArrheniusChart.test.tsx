import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import type { ReactionKineticsRecord } from "../api/reactionEntryApi"
import { ARRHENIUS_CHART_HEIGHT, ARRHENIUS_CHART_WIDTH } from "../domain/arrheniusChartLayout"
import { arrheniusTermK } from "../domain/kineticsTable"
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

// A NARROWER record than `bimolecularRecord()`'s 300-3000 K, sharing its
// A_units so the two land in the SAME panel -- for the per-record range
// clipping tests below. Review finding: every OTHER fixture in this file
// uses one shared 300-3000 K range, so a bug that sampled a record across
// the panel's own wider union domain (instead of its own range) would
// change nothing observable in any of them.
function narrowRecord(overrides: Record<string, unknown> = {}): ReactionKineticsRecord {
    return bimolecularRecord({
        kinetics_ref: "kin_narrow_range",
        temperature_coverage: { record_min_k: 1000, record_max_k: 2000 },
        ...overrides,
    })
}

/** Derives a linear pixel<->value mapping from two ticks' own RENDERED
 *  pixel position and printed label -- the mapping a reader would work out
 *  by looking at the axis, never the component's or the layout module's
 *  own scale function. Used to check that plotted points land where the
 *  TICKS say they should, not merely where the same scale function that
 *  drew them would also redraw them (the review finding this replaces: a
 *  test that recomputes a point from the layout module can't see the axis
 *  and the curve disagreeing, if both re-run the same scale). */
function deriveLinearMapping(ticks: { value: number, pixel: number }[]): (value: number) => number {
    const first = ticks[0]
    const last = ticks[ticks.length - 1]
    const slope = (last.pixel - first.pixel) / (last.value - first.value)
    const intercept = first.pixel - slope * first.value
    return (value: number) => slope * value + intercept
}

function readTicks(svg: HTMLElement | SVGSVGElement, axisClass: string, pixelAttr: "x" | "y"): { value: number, pixel: number }[] {
    return Array.from(svg.querySelectorAll(`.${axisClass}`)).map((el) => ({
        value: Number(el.textContent),
        pixel: Number(el.getAttribute(pixelAttr)),
    }))
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

// This is the PR's headline fix, and the review's blocking finding: the
// PREVIOUS version of this test recomputed a point's expected pixel
// position by calling `arrheniusChartLayout`'s OWN domain functions and
// `chartScale`'s OWN `linearScale` -- the exact same functions the
// component itself uses to place the ticks. A mutation that gave the
// ticks a differently-shifted scale from the curve (e.g. a 300 K-shifted x
// domain for the ticks only) left that test green: it never looked at
// what the ticks actually say. This version reads the ticks' own rendered
// `x`/`y` attribute and printed label straight out of the DOM, derives the
// pixel<->value mapping a READER would derive from the axis, and checks
// the curve against THAT.
describe("ArrheniusChart -- the plotted curve lands on the mapping a reader would derive from the rendered axis ticks", () => {
    it("the curve's known endpoints (300 K / 3000 K; hand-computed log10 k) land within 1px of the tick-derived x/y mapping", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const svg = screen.getByRole("img", { name: /Arrhenius plot/ })

        const xTicks = readTicks(svg, "arrhenius-chart-tick-label--x", "x")
        const yTicks = readTicks(svg, "arrhenius-chart-tick-label--y", "y")
        expect(xTicks.length).toBeGreaterThanOrEqual(2)
        expect(yTicks.length).toBeGreaterThanOrEqual(2)

        const xFromTicks = deriveLinearMapping(xTicks)
        const yFromTicks = deriveLinearMapping(yTicks)

        const polyline = screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")
        const renderedPoints = polyline.getAttribute("points")!.split(" ").map((pair) => pair.split(",").map(Number))
        expect(renderedPoints).toHaveLength(60)
        const [firstX, firstY] = renderedPoints[0]
        const [lastX, lastY] = renderedPoints[renderedPoints.length - 1]

        // Fixture-known endpoints (`computeArrheniusSeries` samples from
        // `record_min_k` to `record_max_k` inclusive -- pinned separately in
        // `arrheniusChartLayout.test.ts`) and the SAME hand-computed log10 k
        // values `kineticsTable.test.ts` pins -- none of this comes from the
        // layout module's scale functions.
        expect(Math.abs(firstX - xFromTicks(300))).toBeLessThanOrEqual(1)
        expect(Math.abs(lastX - xFromTicks(3000))).toBeLessThanOrEqual(1)
        expect(Math.abs(firstY - yFromTicks(4.231179435983835))).toBeLessThanOrEqual(1)
        expect(Math.abs(lastY - yFromTicks(13.60710519570258))).toBeLessThanOrEqual(1)
    })

    it("an INTERIOR sampled point (index 30) also lands on the tick-derived mapping, not just the endpoints", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const svg = screen.getByRole("img", { name: /Arrhenius plot/ })
        const xFromTicks = deriveLinearMapping(readTicks(svg, "arrhenius-chart-tick-label--x", "x"))
        const yFromTicks = deriveLinearMapping(readTicks(svg, "arrhenius-chart-tick-label--y", "y"))

        const polyline = screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")
        const renderedPoints = polyline.getAttribute("points")!.split(" ").map((pair) => pair.split(",").map(Number))
        const [renderedX, renderedY] = renderedPoints[30]

        // Sample index 30's own temperature, from the FIXTURE's sampling
        // formula (60 points, 300-3000 K inclusive) -- and its k(T)/log10 k
        // via `arrheniusTermK` (`kineticsTable.ts`'s pinned equation, reused
        // rather than re-derived). This is the physics equation, not a
        // pixel-scale function -- using it here checks "does the curve agree
        // with the axis", not "does a scale function agree with itself".
        const temperatureK = 300 + (30 * (3000 - 300)) / 59
        const k = arrheniusTermK(3025.44, 3.11242, 39.9711, temperatureK)
        const log10k = Math.log10(k)

        expect(Math.abs(renderedX - xFromTicks(temperatureK))).toBeLessThanOrEqual(1)
        expect(Math.abs(renderedY - yFromTicks(log10k))).toBeLessThanOrEqual(1)
    })
})

// Review finding (blocking #2): every OTHER fixture in this file shares
// bimolecularRecord()'s 300-3000 K range, so nothing here previously
// exercised "one record narrower than the panel it's drawn in". This pairs
// a narrow (1000-2000 K) record with the wide (300-3000 K) one in the SAME
// panel and checks the narrow curve's own rendered pixel extent stays
// strictly inside the wide curve's -- a purely relational, DOM-only check,
// with no dependency on any scale function at all.
describe("ArrheniusChart -- a narrower record's curve is clipped to its own range when panelled beside a wider one", () => {
    it("the narrow record's rendered x-extent is a strict subset of the wide record's, and roughly matches the hand-computed fraction of the shared axis", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord(), narrowRecord()]} />)
        expect(screen.getAllByRole("img", { name: /Arrhenius plot/ })).toHaveLength(1) // same A_units -> one shared panel/axis

        const wideXs = screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")
            .getAttribute("points")!.split(" ").map((pair) => Number(pair.split(",")[0]))
        const narrowXs = screen.getByTestId("arrhenius-line-kin_narrow_range")
            .getAttribute("points")!.split(" ").map((pair) => Number(pair.split(",")[0]))

        const wideMin = Math.min(...wideXs)
        const wideMax = Math.max(...wideXs)
        const narrowMin = Math.min(...narrowXs)
        const narrowMax = Math.max(...narrowXs)

        // The wide record spans the panel's own union domain (300-3000 K)
        // edge to edge; if the narrow record were (incorrectly) sampled
        // across that SAME union domain instead of its own 1000-2000 K, its
        // pixel extent would equal the wide record's, not sit strictly
        // inside it.
        expect(narrowMin).toBeGreaterThan(wideMin)
        expect(narrowMax).toBeLessThan(wideMax)

        // Hand-computed sanity check, independent of any scale function:
        // 1000 K is (1000-300)/(3000-300) = 25.93% of the way across the
        // shared 300-3000 K union domain; 2000 K is 62.96% of the way
        // across. The wide record's own pixel span (wideMin..wideMax) IS
        // that domain's full pixel span, so the narrow record's pixels
        // should fall at roughly those same fractions along it.
        const span = wideMax - wideMin
        const expectedNarrowMin = wideMin + span * ((1000 - 300) / (3000 - 300))
        const expectedNarrowMax = wideMin + span * ((2000 - 300) / (3000 - 300))
        expect(Math.abs(narrowMin - expectedNarrowMin)).toBeLessThanOrEqual(1)
        expect(Math.abs(narrowMax - expectedNarrowMax)).toBeLessThanOrEqual(1)
    })
})

describe("ArrheniusChart -- the SVG is pinned to a fixed pixel size (defect (b) regression guard)", () => {
    it("renders real width/height SVG attributes equal to the layout constants, never a percentage", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const svg = screen.getByRole("img", { name: /Arrhenius plot/ })
        expect(svg.getAttribute("width")).toBe(String(ARRHENIUS_CHART_WIDTH))
        expect(svg.getAttribute("height")).toBe(String(ARRHENIUS_CHART_HEIGHT))
        expect(svg.getAttribute("width")).not.toMatch(/%/)
    })
})

// Review finding (should-fix #5): only the aria-label was ever pinned to
// the record's OWN A_units in a test -- the panel heading and the table's
// own caption/header are the VISIBLE statements of what unit a reader is
// looking at, and neither was checked against a non-cm3_mol_s fixture. A
// component that hardcoded "cm³ mol⁻¹ s⁻¹" in either place would have
// stayed green.
describe("ArrheniusChart -- the panel heading and table caption/header are pinned to the record's OWN A_units, not hardcoded", () => {
    it("a per_s record's panel heading, table caption, and table header all read s⁻¹, not cm³ mol⁻¹ s⁻¹", () => {
        render(<ArrheniusChart kinetics={[unimolecularRecord()]} />)
        expect(screen.getByText("s⁻¹", { selector: ".arrhenius-chart-panel-heading" })).toBeInTheDocument()

        const table = document.querySelector(".kinetics-k-table") as HTMLTableElement
        expect(table.querySelector("caption")?.textContent).toMatch(/, in s⁻¹$/)
        expect(table.querySelector("thead th:nth-child(2)")?.textContent).toBe("k (s⁻¹)")
        expect(table.querySelector("caption")?.textContent).not.toMatch(/cm³/)
    })

    it("a cm3_mol_s record's panel heading, table caption, and table header all read cm³ mol⁻¹ s⁻¹, not s⁻¹", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        expect(screen.getByText("cm³ mol⁻¹ s⁻¹", { selector: ".arrhenius-chart-panel-heading" })).toBeInTheDocument()

        const table = document.querySelector(".kinetics-k-table") as HTMLTableElement
        expect(table.querySelector("caption")?.textContent).toMatch(/, in cm³ mol⁻¹ s⁻¹$/)
        expect(table.querySelector("thead th:nth-child(2)")?.textContent).toBe("k (cm³ mol⁻¹ s⁻¹)")
    })
})

// SCIENTIFIC ERROR regression guard (should-fix #6): `--type-label-transform`
// (design-system.css) is `uppercase`, which every OTHER caller of that
// token wants (an English prose label) -- but this heading's text is a
// UNIT STRING, and upper-casing "s⁻¹" renders "S⁻¹", siemens per second,
// a genuinely different physical quantity. A `textContent` assertion
// cannot see this (the DOM text is still lowercase; only the COMPUTED
// style differs) -- PR 2 shipped exactly this class of rendering-blind bug
// once already (`ReactionEntryPage.test.tsx`'s own "k(T) table header is
// NOT uppercased" test and comment), so this asserts `getComputedStyle`,
// per this project's `vite.config.ts` `test.css: true`.
describe("ArrheniusChart -- the panel heading is never visually uppercased (a unit string, not a prose label)", () => {
    it("computed text-transform is none on .arrhenius-chart-panel-heading, for a per_s record", () => {
        render(<ArrheniusChart kinetics={[unimolecularRecord()]} />)
        const heading = document.querySelector(".arrhenius-chart-panel-heading") as HTMLElement
        expect(heading.textContent).toBe("s⁻¹")
        expect(getComputedStyle(heading).textTransform).toBe("none")
    })
})

// Should-fix #7, the CHART's own half of the third-body table-withholding
// fix (`kineticsTable.test.ts` covers `computeKineticsTable` itself) --
// this checks BOTH surfaces together on one third-body fixture: excluded
// from the plot AND from the table, for the same stated reason.
describe("ArrheniusChart -- a third-body record gets neither a curve nor a k(T) table", () => {
    it("is excluded from the chart (no polyline) and produces no .kinetics-k-table at all", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord({ kinetics_ref: "kin_third_body_only", is_third_body: true })]} />)
        expect(screen.queryByRole("img")).not.toBeInTheDocument()
        expect(screen.queryByTestId("arrhenius-line-kin_third_body_only")).not.toBeInTheDocument()
        expect(document.querySelector(".kinetics-k-table")).toBeNull()
        const note = screen.getByText(/is not plotted/)
        expect(note.textContent).toContain("kin_third_body_only")
        expect(note.textContent).toMatch(/third-body/)
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
