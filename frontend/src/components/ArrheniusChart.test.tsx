import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { ReactionKineticsRecord } from "../api/reactionEntryApi"
import { ARRHENIUS_CHART_HEIGHT, ARRHENIUS_CHART_WIDTH } from "../domain/arrheniusChartLayout"
import { arrheniusTermK, scientificText } from "../domain/kineticsTable"
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

// SAME A/n/Ea/range as `bimolecularRecord()`, but deposited in `m3_mol_s`
// instead of `cm3_mol_s` -- BOTH order 2 (bimolecular), so this shares
// `bimolecularRecord()`'s own panel (the defect this PR fixes: these used
// to be two uncomparable panels). Because the underlying A/n/Ea numbers are
// IDENTICAL, this record's raw (pre-conversion) k(T) curve is numerically
// identical to `bimolecularRecord()`'s -- so once the panel's default
// display unit (`cm3_mol_s`, tie-broken to whichever is served first)
// converts THIS record's own m3_mol_s points by 1e6, the two rendered
// curves must visibly differ. If they render IDENTICALLY, the conversion
// was silently skipped for this series -- a real mutation-catching shape,
// not just a pixel-position check.
function m3Record(overrides: Record<string, unknown> = {}): ReactionKineticsRecord {
    return bimolecularRecord({
        kinetics_ref: "kin_m3_one",
        parameters: { A: 3025.44, A_units: "m3_mol_s", n: 3.11242, Ea_kj_mol: 39.9711 },
        ...overrides,
    })
}

// A termolecular (order 3) record -- own A_units family, own panel, never
// merged with a bimolecular (order 2) or unimolecular (order 1) one.
function termolecularRecord(overrides: Record<string, unknown> = {}): ReactionKineticsRecord {
    return bimolecularRecord({
        kinetics_ref: "kin_termolecular_one",
        parameters: { A: 3025.44, A_units: "cm6_mol2_s", n: 3.11242, Ea_kj_mol: 39.9711 },
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
// the record's OWN A_units in a test -- the y-axis title and the table's
// own caption/header are the VISIBLE statements of what unit a reader is
// looking at, and neither was checked against a non-cm3_mol_s fixture. A
// component that hardcoded "cm³ mol⁻¹ s⁻¹" in either place would have
// stayed green.
//
// Round 3 (owner's report -- "when i do change units, it does change y
// axis but it stil shows log10k there on the side"): the y-axis title is
// now the primary statement of the plotted unit (`log₁₀ [k / <unit>]`),
// replacing the old separate `.arrhenius-chart-panel-heading` caption
// (retired -- restating the same unit a second time was the owner's other
// complaint this round, "display units is confusing... can also be read
// for x units"). These assertions read the axis title directly rather
// than the retired heading class.
describe("ArrheniusChart -- the y-axis title and table caption/header are pinned to the record's OWN A_units, not hardcoded", () => {
    it("a per_s record's y-axis title, table caption, and table header all read s⁻¹, not cm³ mol⁻¹ s⁻¹", () => {
        render(<ArrheniusChart kinetics={[unimolecularRecord()]} />)
        expect(screen.getByText("log₁₀ [k / s⁻¹]", { selector: ".arrhenius-chart-axis-title--y" })).toBeInTheDocument()

        const table = document.querySelector(".kinetics-k-table") as HTMLTableElement
        expect(table.querySelector("caption")?.textContent).toMatch(/, in s⁻¹$/)
        expect(table.querySelector("thead th:nth-child(2)")?.textContent).toBe("k (s⁻¹)")
        expect(table.querySelector("caption")?.textContent).not.toMatch(/cm³/)
    })

    it("a cm3_mol_s record's y-axis title, table caption, and table header all read cm³ mol⁻¹ s⁻¹, not s⁻¹", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        expect(screen.getByText("log₁₀ [k / cm³ mol⁻¹ s⁻¹]", { selector: ".arrhenius-chart-axis-title--y" })).toBeInTheDocument()

        const table = document.querySelector(".kinetics-k-table") as HTMLTableElement
        expect(table.querySelector("caption")?.textContent).toMatch(/, in cm³ mol⁻¹ s⁻¹$/)
        expect(table.querySelector("thead th:nth-child(2)")?.textContent).toBe("k (cm³ mol⁻¹ s⁻¹)")
    })

    // The old standalone unit caption above the legend is gone -- once the
    // axis itself names the unit, that heading stated the same fact twice
    // (owner: "this page stating the same thing twice").
    it("renders no separate .arrhenius-chart-panel-heading caption any more", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        expect(document.querySelector(".arrhenius-chart-panel-heading")).toBeNull()
    })

    // MUTATION TARGET (c) -- "let the table caption and the axis title
    // disagree": the two surfaces are driven from independent code paths
    // (`ArrheniusPanelChart`'s own `displayUnits` for the axis;
    // `displayUnitsByRef` for the table), so nothing in the TYPE SYSTEM
    // stops a future edit from wiring one to the panel's default unit and
    // the other to the live selection. This test switches the unit and
    // then reads BOTH surfaces together, so a regression that updates only
    // one of them fails here even if each surface's own single-unit test
    // above still happens to pass on the default selection.
    it("switching the panel's unit keeps the axis title and the table caption reading the SAME unit", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        fireEvent.change(screen.getByRole("combobox", { name: /Y-axis/ }), { target: { value: "m3_mol_s" } })

        const yTitle = document.querySelector(".arrhenius-chart-axis-title--y") as HTMLElement
        expect(yTitle.textContent).toBe("log₁₀ [k / m³ mol⁻¹ s⁻¹]")

        const caption = document.querySelector(".kinetics-k-table caption")
        expect(caption?.textContent).toMatch(/, in m³ mol⁻¹ s⁻¹$/)
        expect(caption?.textContent).not.toMatch(/cm³/)
    })
})

// SCIENTIFIC ERROR regression guard (post-merge review finding): the
// y-axis title's own text carries a rate-coefficient unit (e.g. "log₁₀
// [k / s⁻¹]") -- the shared `.arrhenius-chart-axis-title` rule inherits
// `--type-label-transform` (`uppercase`), which would render it "LOG₁₀ [K
// / S⁻¹]", both "K" (kelvin, not the rate coefficient) and "S⁻¹" (siemens,
// not seconds⁻¹) reading as the wrong physical quantity on a plot whose
// x-axis reads "TEMPERATURE (K)". A `textContent` assertion cannot see
// this (the DOM text is still lowercase); this asserts `getComputedStyle`,
// per `vite.config.ts`'s `test.css: true`.
describe("ArrheniusChart -- the y-axis title (log10 k / unit) is never visually uppercased into kelvin/siemens", () => {
    it("computed text-transform is none on .arrhenius-chart-axis-title--y, textContent stays lowercase log10 k / unit", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const yTitle = document.querySelector(".arrhenius-chart-axis-title--y") as HTMLElement
        expect(yTitle.textContent).toBe("log₁₀ [k / cm³ mol⁻¹ s⁻¹]")
        expect(getComputedStyle(yTitle).textTransform).toBe("none")
    })

    // The X title is ordinary prose ("Temperature (K)") where uppercasing
    // carries no scientific-error risk -- this pins that the y-axis fix is
    // scoped to `--y` only, never a page-wide removal of the shared
    // `.arrhenius-chart-axis-title` rule's own uppercase behaviour. jsdom
    // does not resolve `var(--type-label-transform)` to a literal keyword
    // (unlike a real browser -- confirmed here: the computed value comes
    // back as the unresolved token string, not "uppercase"), so this
    // cannot assert the literal keyword the way the `--y` test above
    // asserts `"none"`. It instead asserts the one thing jsdom CAN
    // distinguish: the x-axis title's computed value is still the
    // inherited `var(...)` token, NOT the explicit `"none"` an
    // over-broadly-scoped fix (accidentally widening `--y`'s override to
    // the shared `.arrhenius-chart-axis-title` base rule) would produce.
    it("the X-axis title (ordinary prose) is NOT overridden to text-transform: none -- the y-axis fix stays scoped to --y", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const xTitle = document.querySelector(".arrhenius-chart-axis-title--x") as HTMLElement
        expect(xTitle.textContent).toBe("Temperature (K)")
        expect(getComputedStyle(xTitle).textTransform).not.toBe("none")
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

// ---------------------------------------------------------------------------
// New in this PR: per-order-family panel merging, the per-panel unit
// selector, the x-axis mode control, and the k(T) table following the same
// selection as the chart.
// ---------------------------------------------------------------------------

describe("ArrheniusChart -- records sharing an order family (not just an A_units token) share ONE panel", () => {
    it("a cm3_mol_s record and an m3_mol_s record render as ONE svg with two curves, not two panels", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord(), m3Record()]} />)
        expect(screen.getAllByRole("img", { name: /Arrhenius plot/ })).toHaveLength(1)
        expect(screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")).toBeInTheDocument()
        expect(screen.getByTestId("arrhenius-line-kin_m3_one")).toBeInTheDocument()
    })

    // MUTATION-CATCHING (guards against a silently-skipped conversion, not
    // just a pixel position): both records share the exact same A/n/Ea, so
    // their RAW k(T) curves are numerically identical. Once the panel's
    // shared display unit (cm3_mol_s, since it is served first and ties
    // with m3_mol_s 1-1) converts the m3_mol_s record's points by 1e6, the
    // two rendered curves must be visibly DIFFERENT -- identical curves
    // here means the conversion never ran.
    it("the two curves render at DIFFERENT y-positions once the shared panel converts them to its one display unit", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord(), m3Record()]} />)
        const cm3Points = screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq").getAttribute("points")!
        const m3Points = screen.getByTestId("arrhenius-line-kin_m3_one").getAttribute("points")!
        expect(cm3Points).not.toBe(m3Points)
        const cm3FirstY = Number(cm3Points.split(" ")[0].split(",")[1])
        const m3FirstY = Number(m3Points.split(" ")[0].split(",")[1])
        // A 1e6 factor is 6 decades -- nowhere near a rounding-level gap.
        expect(Math.abs(cm3FirstY - m3FirstY)).toBeGreaterThan(10)
    })

    it("a termolecular (cm6_mol2_s) record never shares a panel with a bimolecular (cm3_mol_s) one", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord(), termolecularRecord()]} />)
        expect(screen.getAllByRole("img", { name: /Arrhenius plot/ })).toHaveLength(2)
    })
})

describe("ArrheniusChart -- the per-panel unit selector", () => {
    // Owner's report: hiding the control entirely (the OLD behaviour) is
    // exactly what left the majority of live reaction entries -- 14 of 17
    // deposit in `per_s`, the order family with exactly one member --
    // looking like there was no y-axis control at all. This panel's own
    // control must still be found, DISABLED, showing the one unit it's
    // stuck at, with a plain-language reason next to it.
    it("a per_s panel (family with exactly one member) still renders a Y-axis control -- disabled, showing s⁻¹, with a reason why", () => {
        render(<ArrheniusChart kinetics={[unimolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /Y-axis/ }) as HTMLSelectElement
        expect(select).toBeDisabled()
        const optionLabels = Array.from(select.options).map((option) => option.textContent)
        expect(optionLabels).toEqual(["s⁻¹"])
        expect(select.value).toBe("per_s")

        const note = screen.getByText(/no other unit it could be converted to/)
        expect(note.textContent).toMatch(/unimolecular/)
        expect(note.textContent).toMatch(/s⁻¹/)
        expect(select.getAttribute("aria-describedby")).toBe(note.id)
    })

    // MUTATION TARGET (d) -- "hide the y control when there is no
    // alternative": item (d) in the mutation table checks the control's
    // presence; this checks it looks DELIBERATE rather than broken --
    // owner's round-3 report on visual polish. The disabled chip carries
    // its own modifier class (a quieter fill/border, never a dashed or
    // faded-out look) while staying the same shape/position an enabled
    // control has.
    it("a per_s panel's disabled Y-axis control carries the deliberate-disabled modifier class, not the enabled chip's own styling", () => {
        render(<ArrheniusChart kinetics={[unimolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /Y-axis/ })
        const chip = select.closest(".arrhenius-chart-control") as HTMLElement
        expect(chip.className).toContain("arrhenius-chart-control--disabled")
    })

    it("a bimolecular panel's enabled Y-axis control does NOT carry the disabled modifier class", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /Y-axis/ })
        const chip = select.closest(".arrhenius-chart-control") as HTMLElement
        expect(chip.className).not.toContain("arrhenius-chart-control--disabled")
    })

    it("a bimolecular panel renders an ENABLED selector listing all three order-2 units, base unit first, defaulted to the record's own deposited unit", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /Y-axis \(bimolecular\)/ }) as HTMLSelectElement
        expect(select).not.toBeDisabled()
        const optionLabels = Array.from(select.options).map((option) => option.textContent)
        expect(optionLabels).toEqual(["cm³ mol⁻¹ s⁻¹", "m³ mol⁻¹ s⁻¹", "cm³ molecule⁻¹ s⁻¹"])
        expect(select.value).toBe("cm3_mol_s")
    })

    it("a termolecular panel's selector lists all three order-3 units", () => {
        render(<ArrheniusChart kinetics={[termolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /Y-axis \(termolecular\)/ }) as HTMLSelectElement
        const optionLabels = Array.from(select.options).map((option) => option.textContent)
        expect(optionLabels).toEqual(["cm⁶ mol⁻² s⁻¹", "m⁶ mol⁻² s⁻¹", "cm⁶ molecule⁻² s⁻¹"])
    })

    // MUTATION TARGET (a): the cm³->m³ factor (1e-6, not 1e6). Pinned
    // against the SAME hand-computed value `arrheniusChartLayout.test.ts`
    // and `arrheniusUnits.test.ts` pin: 17028.619287800688 * 1e-6 =
    // 0.017028619287800688.
    it("switching the selector to m3_mol_s re-converts the plotted curve -- pinned against the hand-computed value", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /Y-axis/ })
        fireEvent.change(select, { target: { value: "m3_mol_s" } })

        const svg = screen.getByRole("img", { name: /Arrhenius plot/ })
        expect(svg.getAttribute("aria-label")).toMatch(/m³ mol⁻¹ s⁻¹/)
        expect(screen.getByText("log₁₀ [k / m³ mol⁻¹ s⁻¹]", { selector: ".arrhenius-chart-axis-title--y" })).toBeInTheDocument()

        const yTicks = readTicks(svg, "arrhenius-chart-tick-label--y", "y")
        const yFromTicks = deriveLinearMapping(yTicks)
        const polyline = screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")
        const [, firstY] = polyline.getAttribute("points")!.split(" ")[0].split(",").map(Number)
        const expectedLog10k = Math.log10(0.017028619287800688)
        expect(Math.abs(firstY - yFromTicks(expectedLog10k))).toBeLessThanOrEqual(1)
    })

    // MUTATION TARGET (b): dropping the N_A division for the per-molecule
    // unit. Pinned against 17028.619287800688 / 6.02214076e23 =
    // 2.827668758742313e-20 (same value `arrheniusChartLayout.test.ts` pins).
    it("switching the selector to cm3_molecule_s divides by N_A, not left unconverted", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /Y-axis/ })
        fireEvent.change(select, { target: { value: "cm3_molecule_s" } })

        const svg = screen.getByRole("img", { name: /Arrhenius plot/ })
        const yTicks = readTicks(svg, "arrhenius-chart-tick-label--y", "y")
        const yFromTicks = deriveLinearMapping(yTicks)
        const polyline = screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")
        const [, firstY] = polyline.getAttribute("points")!.split(" ")[0].split(",").map(Number)
        const expectedLog10k = Math.log10(2.827668758742313e-20)
        expect(Math.abs(firstY - yFromTicks(expectedLog10k))).toBeLessThanOrEqual(1)
    })

    // A record's DEPOSITED unit must remain visible regardless of which
    // unit the panel is currently showing (this PR's own invariant).
    it("the legend still names the record's OWN deposited unit after switching the panel's display unit away from it", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        fireEvent.change(screen.getByRole("combobox", { name: /Y-axis/ }), { target: { value: "m3_mol_s" } })
        const legendChip = screen.getByTestId("arrhenius-legend-kin_spkzatwjlvmmnja3i5im4fl7hq")
        expect(legendChip.textContent).toContain("deposited: cm³ mol⁻¹ s⁻¹")
    })

    // MUTATION TARGET (e): the k(T) table left in the deposited unit while
    // the chart converts. Same pinned m3_mol_s value as the chart test
    // above -- if the table's own conversion were skipped, its k column
    // would still read `scientificText(17028.619287800688)` ("1.7029×10⁴"),
    // not the converted value.
    it("switching the panel's unit also converts the k(T) table -- caption, header, AND values", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        fireEvent.change(screen.getByRole("combobox", { name: /Y-axis/ }), { target: { value: "m3_mol_s" } })

        const table = document.querySelector(".kinetics-k-table") as HTMLTableElement
        expect(table.querySelector("caption")?.textContent).toMatch(/, in m³ mol⁻¹ s⁻¹$/)
        expect(table.querySelector("thead th:nth-child(2)")?.textContent).toBe("k (m³ mol⁻¹ s⁻¹)")
        const firstRowK = table.querySelector("tbody tr td[data-label='k']")?.textContent
        expect(firstRowK).toBe(scientificText(0.017028619287800688))
        expect(firstRowK).not.toBe(scientificText(17028.619287800688))
    })
})

describe("ArrheniusChart -- the x-axis mode control (temperature vs 1000/T)", () => {
    it("defaults to Temperature (K) -- today's view, unchanged", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /X-axis/ }) as HTMLSelectElement
        expect(select.value).toBe("temperature")
        const svg = screen.getByRole("img", { name: /Arrhenius plot/ })
        expect(svg.getAttribute("aria-label")).toMatch(/versus temperature in kelvin/)
    })

    // MUTATION TARGET (c): plotting T instead of 1000/T in the inverse
    // mode. Pinned against 1000/300 = 3.3333... and 1000/3000 = 0.3333...
    // -- and the axis is reversed (high T at the LEFT), so the 300 K point
    // (index 0) must land at the tick-derived mapping's HIGH x-value, not
    // its low one.
    it("switching to 1000/T re-projects every plotted point, high temperature at the left, pinned against 1000/T", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        fireEvent.change(screen.getByRole("combobox", { name: /X-axis/ }), { target: { value: "inverse_temperature" } })

        const svg = screen.getByRole("img", { name: /Arrhenius plot/ })
        expect(svg.getAttribute("aria-label")).toMatch(/1000 divided by temperature/)
        expect(svg.getAttribute("aria-label")).toMatch(/straight line/)
        expect(screen.getByText("1000 / T (K⁻¹)", { selector: ".arrhenius-chart-axis-title--x" })).toBeInTheDocument()

        const xTicks = readTicks(svg, "arrhenius-chart-tick-label--x", "x")
        const xFromTicks = deriveLinearMapping(xTicks)
        const polyline = screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")
        const points = polyline.getAttribute("points")!.split(" ").map((pair) => pair.split(",").map(Number))
        const [firstX] = points[0] // T = 300 K -> 1000/T = 3.3333...
        const [lastX] = points[points.length - 1] // T = 3000 K -> 1000/T = 0.3333...

        expect(Math.abs(firstX - xFromTicks(1000 / 300))).toBeLessThanOrEqual(1)
        expect(Math.abs(lastX - xFromTicks(1000 / 3000))).toBeLessThanOrEqual(1)
        // High temperature (3000 K, the LAST point) at the left: its pixel
        // x must be SMALLER than the low-temperature (300 K, first) point's.
        expect(lastX).toBeLessThan(firstX)
    })

    it("switching back to Temperature (K) restores the un-reversed axis", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /X-axis/ })
        fireEvent.change(select, { target: { value: "inverse_temperature" } })
        fireEvent.change(select, { target: { value: "temperature" } })

        const svg = screen.getByRole("img", { name: /Arrhenius plot/ })
        const polyline = screen.getByTestId("arrhenius-line-kin_spkzatwjlvmmnja3i5im4fl7hq")
        const points = polyline.getAttribute("points")!.split(" ").map((pair) => pair.split(",").map(Number))
        const [firstX] = points[0]
        const [lastX] = points[points.length - 1]
        expect(firstX).toBeLessThan(lastX) // 300 K back on the left
        expect(svg.getAttribute("aria-label")).toMatch(/versus temperature in kelvin/)
    })

    it("the x-axis mode is a SINGLE control governing every panel at once", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord(), unimolecularRecord()]} />)
        expect(screen.getAllByRole("combobox", { name: /X-axis/ })).toHaveLength(1)
    })
})

// Owner's report, round 1: "X-AXIS" sat at the far left of the chart block
// while "DISPLAY UNITS" floated to the far right of the same row, reading
// as two unrelated controls rather than a pair. Round 2, on the fix for
// round 1: it rebuilt the pairing as a titled "Chart controls" box
// followed by a SECOND boxed section with a full-sentence "this panel
// only" caption underneath -- on THIS page there is exactly one panel, so
// neither caption disambiguated anything, and the owner pasted the actual
// rendered text back ("this looks so shit"). This describe block covers
// the compact rebuild: a single panel collapses X+Y into ONE row with no
// captions at all; two or more panels keep X-axis as its own single
// (still page-wide, never duplicated) row, with each panel's own Y-axis
// row below it, now naming its scope in two words rather than a sentence
// -- and only because a second panel actually exists to name it against.
describe("ArrheniusChart -- the x-axis and y-axis controls read as a compact pair, not a wall of chrome", () => {
    it("a single panel renders X-axis and Y-axis together in ONE row, with no scope caption at all (mutation table item (d))", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)

        const xSelect = screen.getByRole("combobox", { name: /X-axis/ })
        const ySelect = screen.getByRole("combobox", { name: /Y-axis/ })
        const row = xSelect.closest(".arrhenius-chart-controls") as HTMLElement
        expect(row).not.toBeNull()
        expect(row.contains(ySelect)).toBe(true)
        // Exactly one controls row on the whole page -- X and Y share it.
        expect(document.querySelectorAll(".arrhenius-chart-controls")).toHaveLength(1)

        // Nothing left in the layout to disambiguate -- neither the old
        // full sentences nor a short replacement should appear anywhere.
        expect(screen.queryByText("applies to every panel below")).toBeNull()
        expect(screen.queryByText("this panel only")).toBeNull()
        expect(screen.queryByText("Chart controls")).toBeNull()
        expect(document.querySelectorAll(".arrhenius-chart-controls-scope")).toHaveLength(0)
    })

    it("a mixed-family (2-panel) page keeps X-axis as its own single page-wide row and gives each panel a separate, scoped Y-axis row", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord(), unimolecularRecord()]} />)

        expect(screen.getAllByRole("combobox", { name: /X-axis/ })).toHaveLength(1)
        expect(screen.getAllByRole("combobox", { name: /Y-axis/ })).toHaveLength(2)
        // Three rows total: one X-axis row, two Y-axis rows (one per panel).
        expect(document.querySelectorAll(".arrhenius-chart-controls")).toHaveLength(3)

        // Now that a second panel exists, the compact scope words show up
        // -- short, not the old full sentences.
        expect(screen.getByText("all panels")).toBeInTheDocument()
        expect(screen.getAllByText("this panel")).toHaveLength(2)
        expect(screen.queryByText("applies to every panel below")).toBeNull()
        expect(screen.queryByText("this panel only")).toBeNull()
    })
})

// Owner's report on the no-alternative-unit case specifically: the
// previous round's fix kept the control present (correct) but printed
// `yAxisUnitNote`'s full sentence directly in the page layout. The
// requirement standing from that round -- "never silently omit the y
// control" -- is unchanged; what moved is only where the REASON lives.
describe("ArrheniusChart -- the no-alternative-unit reason is compact and on-demand, never a sentence in the layout", () => {
    it("a per_s panel's disabled Y-axis control shows no full-sentence reason in the visible layout", () => {
        render(<ArrheniusChart kinetics={[unimolecularRecord()]} />)
        const select = screen.getByRole("combobox", { name: /Y-axis/ }) as HTMLSelectElement
        expect(select).toBeDisabled()
        expect(select.options).toHaveLength(1)
        expect(select.options[0].textContent).toBe("s⁻¹")

        // The reason text still exists in the DOM (for aria-describedby /
        // screen readers), but it must be visually hidden, not printed as
        // a paragraph in the flow the way it was before this fix.
        const reasonText = screen.getByText(/no other unit it could be converted to/)
        expect(reasonText.className).toContain("arrhenius-chart-visually-hidden")
    })

    it("gives a real, focusable button carrying the reason -- reachable without a mouse (title + aria-describedby)", () => {
        render(<ArrheniusChart kinetics={[unimolecularRecord()]} />)
        const button = screen.getByRole("button", { name: /Why this unit can't be changed/ })
        expect(button).toHaveAttribute("title", expect.stringContaining("no other unit it could be converted to"))
        const describedById = button.getAttribute("aria-describedby")
        expect(describedById).toBeTruthy()
        expect(document.getElementById(describedById!)?.textContent).toMatch(/no other unit it could be converted to/)
        // A real button is in the tab order by default (no explicit
        // tabindex=-1 / disabled) -- unlike the select it explains, which
        // IS disabled and therefore unreachable by keyboard at all.
        expect(button).not.toHaveAttribute("disabled")
        expect(button.getAttribute("tabindex")).not.toBe("-1")
    })

    it("a bimolecular panel (real unit choice) renders no reason button at all", () => {
        render(<ArrheniusChart kinetics={[bimolecularRecord()]} />)
        expect(screen.queryByRole("button", { name: /Why this unit/ })).toBeNull()
    })
})
