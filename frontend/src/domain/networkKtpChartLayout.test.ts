import { describe, expect, it } from "vitest"
import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import type { NetworkKtpFit } from "../api/networkKineticsEvalApi"
import {
    buildKtpRequestGrid,
    groupKtpFitsByChannel,
    KTP_PRESSURE_POINT_COUNT,
    KTP_TEMPERATURE_POINT_COUNT,
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

describe("modelKindLabel", () => {
    it("renders a human label for each known model kind", () => {
        expect(modelKindLabel("chebyshev")).toBe("Chebyshev")
        expect(modelKindLabel("plog")).toBe("PLOG")
    })
})

// ---------------------------------------------------------------------------
// ktpPlottedPoints -- filters to one pressure, never re-evaluates k
// ---------------------------------------------------------------------------
