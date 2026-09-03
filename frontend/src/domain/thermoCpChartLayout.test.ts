import { describe, expect, it } from "vitest"
import type { CpChartSeries } from "./thermoCpSeries"
import {
    computeTemperatureDomain,
    groupIdenticalSeries,
    groupLegendLabel,
    niceTicks,
} from "./thermoCpChartLayout"

function series(overrides: Partial<CpChartSeries> = {}): CpChartSeries {
    return {
        thermoRef: "thm_x",
        label: "Conformer Group 1",
        isSelected: false,
        hasUsableFit: true,
        hasMeasuredPoints: true,
        measured: [{ temperatureK: 300, cpDisplay: 40 }],
        fitted: { low: [{ temperatureK: 100, cpDisplay: 10 }], high: [{ temperatureK: 900, cpDisplay: 90 }] },
        ...overrides,
    }
}

describe("computeTemperatureDomain", () => {
    it("clamps the low end at 0 K rather than padding outward -- a live record with t_low near 0 K used to pad straight through zero into negative kelvin (the review finding this replaced)", () => {
        const nearZeroRecord = series({
            fitted: { low: [{ temperatureK: 10, cpDisplay: 20 }], high: [{ temperatureK: 3000, cpDisplay: 130 }] },
            measured: [],
        })
        const [low, high] = computeTemperatureDomain([nearZeroRecord])
        expect(low).toBe(0)
        expect(high).toBe(3000)
    })

    it("uses the actual maximum plotted temperature as the high end, unpadded", () => {
        const a = series({ measured: [{ temperatureK: 250, cpDisplay: 30 }], fitted: null })
        const b = series({ measured: [{ temperatureK: 700, cpDisplay: 60 }], fitted: null })
        expect(computeTemperatureDomain([a, b])).toEqual([0, 700])
    })

    it("returns [0, 1] when nothing is plotted anywhere, never a degenerate or negative domain", () => {
        const empty = series({ measured: [], fitted: null })
        expect(computeTemperatureDomain([empty])).toEqual([0, 1])
        expect(computeTemperatureDomain([])).toEqual([0, 1])
    })
})

describe("niceTicks", () => {
    it("matches the review finding's own example: 0, 500, 1000, ... for a 0-3000 domain", () => {
        expect(niceTicks([0, 3000], 5)).toEqual([0, 500, 1000, 1500, 2000, 2500, 3000])
    })

    it("rounds a would-be-fractional step to a round number -- the Cp-axis half of the review finding ('20.8, 49.7, 78.7, 108, 137')", () => {
        // span 126.5 / 5 = 25.3 raw -> the nearest {1,2,5}x10^n step is 20,
        // never a float that reproduces the raw division.
        const ticks = niceTicks([12.4, 138.9], 5)
        expect(ticks).toEqual([20, 40, 60, 80, 100, 120])
        for (let i = 1; i < ticks.length; i += 1) expect(ticks[i] - ticks[i - 1]).toBe(20)
        for (const tick of ticks) {
            expect(tick).toBeGreaterThanOrEqual(12.4)
            expect(tick).toBeLessThanOrEqual(138.9)
        }
    })

    it("returns the single domain start on a zero-span domain, mirroring evenTicks' degenerate case", () => {
        expect(niceTicks([5, 5], 5)).toEqual([5])
    })
})

describe("groupIdenticalSeries", () => {
    it("collapses series whose measured+fitted values are byte-identical into one group, preserving every member", () => {
        const a = series({ thermoRef: "thm_a" })
        const b = series({ thermoRef: "thm_b" })
        const c = series({ thermoRef: "thm_c" })
        const groups = groupIdenticalSeries([a, b, c])
        expect(groups).toHaveLength(1)
        expect(groups[0].representative.thermoRef).toBe("thm_a")
        expect(groups[0].members.map((m) => m.thermoRef)).toEqual(["thm_a", "thm_b", "thm_c"])
    })

    it("never collapses series with genuinely different plotted values, even sharing every other field", () => {
        const a = series({ thermoRef: "thm_a" })
        const differentFit = series({
            thermoRef: "thm_a_v2",
            fitted: { low: [{ temperatureK: 100, cpDisplay: 11 }], high: [{ temperatureK: 900, cpDisplay: 91 }] },
        })
        const groups = groupIdenticalSeries([a, differentFit])
        expect(groups).toHaveLength(2)
        expect(groups[0].members).toHaveLength(1)
        expect(groups[1].members).toHaveLength(1)
    })

    it("preserves first-seen order across groups", () => {
        const a = series({ thermoRef: "thm_a" })
        const b = series({ thermoRef: "thm_b", measured: [{ temperatureK: 999, cpDisplay: 1 }], fitted: null })
        const aAgain = series({ thermoRef: "thm_a2" })
        const groups = groupIdenticalSeries([a, b, aAgain])
        expect(groups.map((g) => g.representative.thermoRef)).toEqual(["thm_a", "thm_b"])
        expect(groups[0].members.map((m) => m.thermoRef)).toEqual(["thm_a", "thm_a2"])
    })

    it("never groups two series that both plot nothing, even though their structural signature ([[],null]) is identical -- each stays its own group so a different conformer group's record is never silently dropped (the blocking review finding)", () => {
        const emptyOnGroupOne = series({
            thermoRef: "thm_empty_1",
            label: "Conformer Group 1",
            hasUsableFit: false,
            hasMeasuredPoints: false,
            measured: [],
            fitted: null,
        })
        const emptyOnGroupTwo = series({
            thermoRef: "thm_empty_2",
            label: "Conformer Group 2",
            hasUsableFit: false,
            hasMeasuredPoints: false,
            measured: [],
            fitted: null,
        })
        const groups = groupIdenticalSeries([emptyOnGroupOne, emptyOnGroupTwo])
        expect(groups).toHaveLength(2)
        expect(groups[0].members).toHaveLength(1)
        expect(groups[1].members).toHaveLength(1)
        expect(groups.map((g) => g.representative.thermoRef)).toEqual(["thm_empty_1", "thm_empty_2"])
    })

    it("never groups two unplottable series from the SAME conformer group either -- identity must key on the record, not the label (a re-review mutation keyed on label passed every existing test)", () => {
        const first = series({
            thermoRef: "thm_empty_a",
            label: "Conformer Group 1",
            hasUsableFit: false,
            hasMeasuredPoints: false,
            measured: [],
            fitted: null,
        })
        const second = series({
            thermoRef: "thm_empty_b",
            label: "Conformer Group 1",
            hasUsableFit: false,
            hasMeasuredPoints: false,
            measured: [],
            fitted: null,
        })
        const groups = groupIdenticalSeries([first, second])
        expect(groups).toHaveLength(2)
        expect(groups.map((g) => g.representative.thermoRef)).toEqual(["thm_empty_a", "thm_empty_b"])
    })

    it("still collapses several genuinely byte-identical PLOTTABLE series sharing one conformer, even alongside an unrelated empty series -- the fix only carves out the unplottable case", () => {
        const a = series({ thermoRef: "thm_a" })
        const b = series({ thermoRef: "thm_b" })
        const emptyElsewhere = series({
            thermoRef: "thm_empty",
            label: "Conformer Group 2",
            hasUsableFit: false,
            hasMeasuredPoints: false,
            measured: [],
            fitted: null,
        })
        const groups = groupIdenticalSeries([a, b, emptyElsewhere])
        expect(groups).toHaveLength(2)
        expect(groups[0].members.map((m) => m.thermoRef)).toEqual(["thm_a", "thm_b"])
        expect(groups[1].members).toHaveLength(1)
    })
})

describe("groupLegendLabel", () => {
    it("names the count for a collapsed group, rather than the bare conformer label seven identical chips used to repeat", () => {
        const members = [series({ thermoRef: "thm_a" }), series({ thermoRef: "thm_b" }), series({ thermoRef: "thm_c" })]
        const group = { representative: members[0], members }
        expect(groupLegendLabel(group, [group])).toBe("Conformer Group 1 — 3 identical records")
    })

    it("leaves a lone, non-colliding group's label untouched", () => {
        const group = { representative: series({ thermoRef: "thm_a" }), members: [series({ thermoRef: "thm_a" })] }
        const other = {
            representative: series({ thermoRef: "thm_b", label: "Conformer Group 2" }),
            members: [series({ thermoRef: "thm_b", label: "Conformer Group 2" })],
        }
        expect(groupLegendLabel(group, [group, other])).toBe("Conformer Group 1")
    })

    it("disambiguates by thermo_ref when two DIFFERENT groups share the same base label", () => {
        const groupA = { representative: series({ thermoRef: "thm_a" }), members: [series({ thermoRef: "thm_a" })] }
        const groupAv2 = {
            representative: series({ thermoRef: "thm_a_v2" }),
            members: [series({ thermoRef: "thm_a_v2" })],
        }
        const all = [groupA, groupAv2]
        expect(groupLegendLabel(groupA, all)).toBe("Conformer Group 1 (thm_a)")
        expect(groupLegendLabel(groupAv2, all)).toBe("Conformer Group 1 (thm_a_v2)")
    })

    it("disambiguates two DIFFERENT multi-member groups sharing the same base label AND the same member count -- not only single-member collisions", () => {
        const groupAMembers = [series({ thermoRef: "thm_a1" }), series({ thermoRef: "thm_a2" })]
        const groupA = { representative: groupAMembers[0], members: groupAMembers }
        const groupBMembers = [
            series({
                thermoRef: "thm_b1",
                fitted: { low: [{ temperatureK: 100, cpDisplay: 11 }], high: [{ temperatureK: 900, cpDisplay: 91 }] },
            }),
            series({
                thermoRef: "thm_b2",
                fitted: { low: [{ temperatureK: 100, cpDisplay: 11 }], high: [{ temperatureK: 900, cpDisplay: 91 }] },
            }),
        ]
        const groupB = { representative: groupBMembers[0], members: groupBMembers }
        const all = [groupA, groupB]
        expect(groupLegendLabel(groupA, all)).toBe("Conformer Group 1 (thm_a1) — 2 identical records")
        expect(groupLegendLabel(groupB, all)).toBe("Conformer Group 1 (thm_b1) — 2 identical records")
    })
})
