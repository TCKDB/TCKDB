import { describe, expect, it } from "vitest"
import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import type { NetworkKtpEvaluatedPoint, NetworkKtpFit } from "../api/networkKineticsEvalApi"
import {
    buildKtpRequestGrid,
    groupKtpFitsByChannel,
    ktpLineSegments,
    ktpPlottedPoints,
    KTP_PRESSURE_POINT_COUNT,
    KTP_TEMPERATURE_POINT_COUNT,
    modelKindColor,
    modelKindLabel,
} from "./networkKtpChartLayout"

function state(hash: string, kind: string, label: string): NetworkState {
    return {
        composition_hash: hash,
        kind,
        label: null,
        participant_count: 1,
        composition: { participants: [], participant_count_total: 1, participants_truncated: false, state_label: label },
    }
}

function channel(key: string, source: string, sink: string): NetworkChannel {
    return {
        channel_key: key,
        kind: "isomerization",
        mechanism: "elementary",
        source_state_composition_hash: source,
        sink_state_composition_hash: sink,
        has_kinetics: true,
        microreactions: [],
    }
}

function point(temperatureK: number, pressureBar: number, k: number, inRange = true): NetworkKtpEvaluatedPoint {
    return { temperature_k: temperatureK, pressure_bar: pressureBar, k, in_range: inRange }
}

function fit(overrides: Partial<NetworkKtpFit> = {}): NetworkKtpFit {
    return {
        network_kinetics_ref: "nk_1",
        channel_key: "channel_1",
        channel_kind: "isomerization",
        source_state_composition_hash: "h2",
        sink_state_composition_hash: "h1",
        network_solve_ref: "nsolve_1",
        model_kind: "chebyshev",
        k_units: "per_s",
        tmin_k: 300,
        tmax_k: 2000,
        pmin_bar: 0.01,
        pmax_bar: 100,
        points: [],
        ...overrides,
    }
}

// ---------------------------------------------------------------------------
// buildKtpRequestGrid
// ---------------------------------------------------------------------------

describe("buildKtpRequestGrid", () => {
    it("spans the full temperature range inclusive of both endpoints", () => {
        const grid = buildKtpRequestGrid(300, 2000, 0.01, 100)
        expect(grid).not.toBeNull()
        expect(grid!.temperaturesK.length).toBe(KTP_TEMPERATURE_POINT_COUNT)
        expect(grid!.temperaturesK[0]).toBeCloseTo(300)
        expect(grid!.temperaturesK.at(-1)).toBeCloseTo(2000)
    })

    it("spans the full pressure range, log-spaced, inclusive of both endpoints", () => {
        const grid = buildKtpRequestGrid(300, 2000, 0.01, 100)
        expect(grid!.pressuresBar.length).toBe(KTP_PRESSURE_POINT_COUNT)
        expect(grid!.pressuresBar[0]).toBeCloseTo(0.01)
        expect(grid!.pressuresBar.at(-1)).toBeCloseTo(100)
        // Log-spaced, not linear: the SECOND point should sit near the
        // geometric, not arithmetic, midpoint between neighbours -- a
        // linear grid across four decades would crowd every point above
        // the lowest one into a tiny sliver near 100.
        expect(grid!.pressuresBar[1]).toBeLessThan(2)
    })

    it("stays within the batch endpoint's per-fit grid cap (200 points)", () => {
        const grid = buildKtpRequestGrid(300, 2000, 0.01, 100)
        expect(grid!.temperaturesK.length * grid!.pressuresBar.length).toBeLessThanOrEqual(200)
    })

    it("returns null when the temperature range is unrecorded -- degrade, never guess", () => {
        expect(buildKtpRequestGrid(null, null, 0.01, 100)).toBeNull()
        expect(buildKtpRequestGrid(300, null, 0.01, 100)).toBeNull()
    })

    it("returns null when the pressure range is unrecorded", () => {
        expect(buildKtpRequestGrid(300, 2000, null, null)).toBeNull()
    })

    it("returns null when a bound is degenerate or inverted", () => {
        expect(buildKtpRequestGrid(300, 300, 0.01, 100)).toBeNull()
        expect(buildKtpRequestGrid(300, 2000, 100, 0.01)).toBeNull()
        expect(buildKtpRequestGrid(300, 2000, 0, 100)).toBeNull()
    })
})

// ---------------------------------------------------------------------------
// groupKtpFitsByChannel -- MUTATION TARGET: collapsing to one fit/channel
// ---------------------------------------------------------------------------

describe("groupKtpFitsByChannel", () => {
    const states = [state("h1", "well", "NN"), state("h2", "bimolecular", "NN + [H][H]")]
    const channels = [channel("channel_1", "h2", "h1")]

    it("keeps BOTH fits for a channel that has a Chebyshev and a PLOG parameterization -- never collapses to one", () => {
        const fits = [
            fit({ network_kinetics_ref: "nk_cheb", model_kind: "chebyshev" }),
            fit({ network_kinetics_ref: "nk_plog", model_kind: "plog" }),
        ]
        const groups = groupKtpFitsByChannel(fits, channels, states)
        expect(groups).toHaveLength(1)
        expect(groups[0].series).toHaveLength(2)
        expect(groups[0].series.map((s) => s.network_kinetics_ref).sort()).toEqual(["nk_cheb", "nk_plog"])
    })

    it("labels the channel by chemistry (source to sink), never by channel_key", () => {
        const groups = groupKtpFitsByChannel([fit()], channels, states)
        expect(groups[0].label).toBe("NN + [H][H] to NN")
        expect(groups[0].label).not.toContain("channel_1")
    })

    it("orders groups the same way channels[] is ordered", () => {
        const twoChannels = [channel("channel_2", "h1", "h2"), channel("channel_1", "h2", "h1")]
        const fits = [
            fit({ network_kinetics_ref: "nk_a", channel_key: "channel_1" }),
            fit({ network_kinetics_ref: "nk_b", channel_key: "channel_2" }),
        ]
        const groups = groupKtpFitsByChannel(fits, twoChannels, states)
        expect(groups.map((g) => g.channelKey)).toEqual(["channel_2", "channel_1"])
    })
})

// ---------------------------------------------------------------------------
// modelKindColor / modelKindLabel -- distinguishability
// ---------------------------------------------------------------------------

describe("modelKindColor", () => {
    it("gives chebyshev and plog different colours, consistently (not per-panel, globally)", () => {
        expect(modelKindColor("chebyshev")).not.toBe(modelKindColor("plog"))
        expect(modelKindColor("chebyshev")).toBe(modelKindColor("chebyshev"))
    })
})

describe("modelKindLabel", () => {
    it("renders a human label for each known model kind", () => {
        expect(modelKindLabel("chebyshev")).toBe("Chebyshev")
        expect(modelKindLabel("plog")).toBe("PLOG")
    })
})

// ---------------------------------------------------------------------------
// ktpPlottedPoints -- filters to one pressure, never re-evaluates k
// ---------------------------------------------------------------------------

describe("ktpPlottedPoints", () => {
    const points = [
        point(300, 1, 10),
        point(300, 10, 100),
        point(400, 1, 20),
        point(400, 10, 200),
    ]

    it("keeps only points at the selected pressure, sorted by temperature", () => {
        const plotted = ktpPlottedPoints(points, 1)
        expect(plotted.map((p) => p.temperatureK)).toEqual([300, 400])
        expect(plotted.every((p) => p.k === 10 || p.k === 20)).toBe(true)
    })

    it("renders the SERVED k verbatim (log10 is display math on the served value, never a re-evaluation)", () => {
        const plotted = ktpPlottedPoints([point(300, 1, 1000)], 1)
        expect(plotted[0].k).toBe(1000)
        expect(plotted[0].log10k).toBeCloseTo(3)
    })

    it("skips a non-positive k rather than plotting an undefined log10", () => {
        const plotted = ktpPlottedPoints([point(300, 1, 0), point(300, 1, -5)], 1)
        expect(plotted).toHaveLength(0)
    })

    it("carries the served in_range flag through untouched", () => {
        const plotted = ktpPlottedPoints([point(300, 1, 10, false)], 1)
        expect(plotted[0].inRange).toBe(false)
    })
})

// ---------------------------------------------------------------------------
// ktpLineSegments -- MUTATION TARGET: in_range must stay visually distinct
// ---------------------------------------------------------------------------

describe("ktpLineSegments", () => {
    it("a fully in-range series is one solid segment", () => {
        const segments = ktpLineSegments([
            { temperatureK: 300, k: 1, log10k: 0, inRange: true },
            { temperatureK: 400, k: 1, log10k: 0, inRange: true },
            { temperatureK: 500, k: 1, log10k: 0, inRange: true },
        ])
        expect(segments).toHaveLength(1)
        expect(segments[0].solid).toBe(true)
        expect(segments[0].points).toHaveLength(3)
    })

    it("splits at an in_range transition, sharing the boundary point so the line stays continuous", () => {
        const segments = ktpLineSegments([
            { temperatureK: 300, k: 1, log10k: 0, inRange: true },
            { temperatureK: 400, k: 1, log10k: 0, inRange: true },
            { temperatureK: 500, k: 1, log10k: 0, inRange: false },
            { temperatureK: 600, k: 1, log10k: 0, inRange: false },
        ])
        expect(segments).toHaveLength(2)
        expect(segments[0].solid).toBe(true)
        expect(segments[0].points.map((p) => p.temperatureK)).toEqual([300, 400])
        expect(segments[1].solid).toBe(false)
        expect(segments[1].points.map((p) => p.temperatureK)).toEqual([400, 500, 600])
    })

    it("an empty point list produces no segments", () => {
        expect(ktpLineSegments([])).toHaveLength(0)
    })
})
