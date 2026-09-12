import { describe, expect, it } from "vitest"
import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import type { NetworkKtpEvaluatedPoint, NetworkKtpFit } from "../api/networkKineticsEvalApi"
import { familyUnits } from "./arrheniusUnits"
import {
    buildKtpRequestGrid,
    channelColorIndex,
    channelSeriesColor,
    convertKtpPlottedPoints,
    groupKtpChannelsByUnitFamily,
    groupKtpFitsByChannel,
    ktpLineSegments,
    ktpPlottedPoints,
    KTP_PRESSURE_POINT_COUNT,
    KTP_TEMPERATURE_POINT_COUNT,
    modelKindColor,
    modelKindLabel,
    modelKindStrokeColor,
    modelKindStrokeWidth,
    type KtpChannelGroup,
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

// ---------------------------------------------------------------------------
// familyUnits(1) -- what this file's brief asked to be CONFIRMED, not
// assumed: does the unimolecular family offer any interchangeable sibling
// unit to convert `per_s` to?
// ---------------------------------------------------------------------------

describe("familyUnits(1) -- the per_s (isomerization) family", () => {
    it("has exactly one member, per_s itself -- no sibling unit exists to convert to", () => {
        // Confirmed directly against arrheniusUnits.ts rather than assumed:
        // a unimolecular rate coefficient is s^-1 by definition, so there is
        // nothing else in its family. `NetworkKtpChart.tsx`'s own
        // `KtpFamilyUnitSelect` is gated on `availableUnits.length > 1` and
        // renders NO control at all for a panel like this one, rather than
        // a disabled control with an empty menu.
        expect(familyUnits(1)).toEqual(["per_s"])
    })

    it("cm3_mol_s's family (order 2) DOES offer more than one member", () => {
        expect(familyUnits(2).length).toBeGreaterThan(1)
        expect(familyUnits(2)).toContain("cm3_mol_s")
    })
})

// ---------------------------------------------------------------------------
// groupKtpChannelsByUnitFamily -- MUTATION TARGET: two different unit
// families must never land in the same panel; a channel's two fits must
// never be collapsed to one, even once channels are grouped a SECOND time
// (by family, on top of by-channel).
// ---------------------------------------------------------------------------

function channelGroup(channelKey: string, label: string, channelKind: string, series: NetworkKtpFit[]): KtpChannelGroup {
    return { channelKey, label, channelKind, series }
}

describe("groupKtpChannelsByUnitFamily", () => {
    it("places a per_s channel and a cm3_mol_s channel in DIFFERENT panels -- different physical dimensions", () => {
        const groups = [
            channelGroup("channel_iso", "NN to [NH2] + [NH2]", "isomerization", [
                fit({ network_kinetics_ref: "nk_iso_cheb", channel_key: "channel_iso", k_units: "per_s" }),
            ]),
            channelGroup("channel_assoc", "[NH2] + [NH2] to NN", "association", [
                fit({ network_kinetics_ref: "nk_assoc_cheb", channel_key: "channel_assoc", k_units: "cm3_mol_s" }),
            ]),
        ]
        const panels = groupKtpChannelsByUnitFamily(groups)
        expect(panels).toHaveLength(2)
        expect(panels[0].orderFamily).toBe(1)
        expect(panels[0].groups.map((g) => g.channelKey)).toEqual(["channel_iso"])
        expect(panels[1].orderFamily).toBe(2)
        expect(panels[1].groups.map((g) => g.channelKey)).toEqual(["channel_assoc"])
        // The scientific constraint itself, spelled out: no panel may hold
        // BOTH a channel_iso fit and a channel_assoc fit at once.
        const channelKeysPerPanel = panels.map((p) => new Set(p.groups.map((g) => g.channelKey)))
        expect(channelKeysPerPanel[0].has("channel_assoc")).toBe(false)
        expect(channelKeysPerPanel[1].has("channel_iso")).toBe(false)
    })

    it("keeps two channels of the SAME family together in one panel, overlaid", () => {
        const groups = [
            channelGroup("channel_a", "A to B", "association", [fit({ network_kinetics_ref: "nk_a", channel_key: "channel_a", k_units: "cm3_mol_s" })]),
            channelGroup("channel_b", "B to C", "exchange", [fit({ network_kinetics_ref: "nk_b", channel_key: "channel_b", k_units: "cm3_mol_s" })]),
        ]
        const panels = groupKtpChannelsByUnitFamily(groups)
        expect(panels).toHaveLength(1)
        expect(panels[0].groups.map((g) => g.channelKey)).toEqual(["channel_a", "channel_b"])
    })

    it("never collapses a channel's two fits (Chebyshev + PLOG) even after regrouping by family", () => {
        const groups = [
            channelGroup("channel_a", "A to B", "association", [
                fit({ network_kinetics_ref: "nk_cheb", channel_key: "channel_a", model_kind: "chebyshev", k_units: "cm3_mol_s" }),
                fit({ network_kinetics_ref: "nk_plog", channel_key: "channel_a", model_kind: "plog", k_units: "cm3_mol_s" }),
            ]),
        ]
        const panels = groupKtpChannelsByUnitFamily(groups)
        expect(panels).toHaveLength(1)
        expect(panels[0].groups).toHaveLength(1)
        expect(panels[0].groups[0].series.map((s) => s.network_kinetics_ref).sort()).toEqual(["nk_cheb", "nk_plog"])
    })

    it("a single-member family (per_s) reports availableUnits with exactly one entry -- the panel HAS a family, just nothing to convert within it", () => {
        // `availableUnits` mirrors `familyUnits(orderFamily)` verbatim, same
        // as `arrheniusChartLayout.ts`'s own `ArrheniusPanel` -- it is the
        // COMPONENT's `hasUnitChoice = availableUnits.length > 1` gate
        // (`NetworkKtpChart.tsx`) that decides whether a control renders at
        // all, not this function silently blanking a single-member family.
        const groups = [channelGroup("channel_iso", "NN to [NH2] + [NH2]", "isomerization", [fit({ k_units: "per_s", channel_key: "channel_iso" })])]
        const panels = groupKtpChannelsByUnitFamily(groups)
        expect(panels[0].availableUnits).toEqual(["per_s"])
        expect(panels[0].defaultUnits).toBe("per_s")
    })

    it("a multi-member family (cm3_mol_s) reports every interchangeable unit, defaulting to the modal one", () => {
        const groups = [
            channelGroup("channel_a", "A to B", "association", [fit({ network_kinetics_ref: "nk_a1", channel_key: "channel_a", k_units: "cm3_mol_s" })]),
            channelGroup("channel_b", "B to C", "exchange", [fit({ network_kinetics_ref: "nk_b1", channel_key: "channel_b", k_units: "m3_mol_s" })]),
            channelGroup("channel_c", "C to D", "association", [fit({ network_kinetics_ref: "nk_c1", channel_key: "channel_c", k_units: "cm3_mol_s" })]),
        ]
        const panels = groupKtpChannelsByUnitFamily(groups)
        expect(panels).toHaveLength(1)
        expect(panels[0].availableUnits).toEqual(["cm3_mol_s", "m3_mol_s", "cm3_molecule_s"])
        // cm3_mol_s deposited twice (channel_a, channel_c), m3_mol_s once -- modal wins.
        expect(panels[0].defaultUnits).toBe("cm3_mol_s")
    })
})

// ---------------------------------------------------------------------------
// convertKtpPlottedPoints -- unit conversion of an ALREADY-SERVED k, never a
// re-evaluation (invariant 3). MUTATION TARGET: this is what makes
// "changing the selected unit converts the plotted VALUES" true at all.
// ---------------------------------------------------------------------------

describe("convertKtpPlottedPoints", () => {
    const points = [
        { temperatureK: 300, k: 1e13, log10k: 13, inRange: true },
        { temperatureK: 400, k: 2e13, log10k: Math.log10(2e13), inRange: false },
    ]

    it("identical units: returns an equal-valued copy, not the same reference", () => {
        const converted = convertKtpPlottedPoints(points, "cm3_mol_s", "cm3_mol_s")
        expect(converted).toEqual(points)
        expect(converted).not.toBe(points)
    })

    it("cm3_mol_s -> m3_mol_s scales k by 1e-6 (1 m^3 = 1e6 cm^3) and recomputes log10k from the SAME converted k", () => {
        const converted = convertKtpPlottedPoints(points, "cm3_mol_s", "m3_mol_s")
        expect(converted[0].k).toBeCloseTo(1e13 * 1e-6)
        expect(converted[0].log10k).toBeCloseTo(Math.log10(converted[0].k))
        expect(converted[1].k).toBeCloseTo(2e13 * 1e-6)
    })

    it("carries in_range through the conversion untouched -- unit conversion never touches range status", () => {
        const converted = convertKtpPlottedPoints(points, "cm3_mol_s", "m3_mol_s")
        expect(converted[0].inRange).toBe(true)
        expect(converted[1].inRange).toBe(false)
    })

    it("a cross-family pair (per_s to cm3_mol_s) is refused -- returns points UNCHANGED, never guessed at", () => {
        const converted = convertKtpPlottedPoints(points, "per_s", "cm3_mol_s")
        expect(converted).toEqual(points)
    })
})

// ---------------------------------------------------------------------------
// Series encoding -- channel identity (colour) and model kind (tint +
// stroke width), kept independent of each other and of the dash encoding.
// ---------------------------------------------------------------------------

describe("channelColorIndex / channelSeriesColor", () => {
    it("assigns each channel a stable index by its position in the given (unfiltered) list", () => {
        const groups = [
            channelGroup("channel_a", "A", "association", []),
            channelGroup("channel_b", "B", "exchange", []),
            channelGroup("channel_c", "C", "isomerization", []),
        ]
        const index = channelColorIndex(groups)
        expect(index.get("channel_a")).toBe(0)
        expect(index.get("channel_b")).toBe(1)
        expect(index.get("channel_c")).toBe(2)
    })

    it("gives different channels different colours (within the 8-colour cycle)", () => {
        expect(channelSeriesColor(0)).not.toBe(channelSeriesColor(1))
    })
})

describe("modelKindStrokeColor / modelKindStrokeWidth -- MUTATION TARGET: model kind must stay distinguishable within one channel's own colour", () => {
    it("chebyshev leaves the base colour untouched (0% tint)", () => {
        expect(modelKindStrokeColor("var(--chart-series-1)", "chebyshev")).toBe("var(--chart-series-1)")
    })

    it("plog tints the SAME base colour toward white -- a different string, same underlying hue", () => {
        const base = "var(--chart-series-1)"
        const plogStroke = modelKindStrokeColor(base, "plog")
        expect(plogStroke).not.toBe(base)
        expect(plogStroke).toContain(base)
    })

    it("for one channel's base colour, chebyshev and plog resolve to different stroke strings -- the same distinctness the old model-kind-only colouring gave, preserved under the new channel-colour scheme", () => {
        const base = channelSeriesColor(0)
        expect(modelKindStrokeColor(base, "chebyshev")).not.toBe(modelKindStrokeColor(base, "plog"))
    })

    it("chebyshev is drawn heavier than plog -- a SECOND, redundant encoding of model kind alongside the tint", () => {
        expect(modelKindStrokeWidth("chebyshev")).toBeGreaterThan(modelKindStrokeWidth("plog"))
    })
})
