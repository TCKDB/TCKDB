import type { ThermoRecord } from "../api/thermoApi"

// ---------------------------------------------------------------------------
// NASA-7 polynomial evaluation, kept as PURE functions and tested
// independently of any rendering (`thermoNasa.test.ts`) -- this is the one
// part of the Cp-vs-T chart that can be confidently WRONG in a way a
// snapshot or a DOM query would never catch: the formula silently using the
// wrong coefficient set, or evaluating a temperature past the fit's own
// stated validity range, both produce a chart that still renders fine and
// still looks like a chart.
//
// NASA-7 form (7 coefficients per temperature range; only the first five
// bear on heat capacity):
//
//     Cp / R = a1 + a2*T + a3*T^2 + a4*T^3 + a5*T^4
//
// split into a LOW range (`t_low` <= T < `t_mid`) and a HIGH range
// (`t_mid` <= T <= `t_high`), each with its own five (of seven) a-values.
// The CHEMKIN convention this file follows puts T == t_mid on the HIGH
// branch -- `nasaCpCurve`/`sampleBranch` below sample each branch over its
// own [from, to) range so the split is never ambiguous at the boundary.
// Getting that split backwards is exactly the bug class
// `thermoNasa.test.ts` is built to catch: with two coefficient sets that
// actually differ, a wrong branch produces a visibly wrong Cp at a given
// T, not a silently-close one.
//
// This file used to also carry `nasaCpAtTemperature`/`cpResiduals` --
// single-temperature evaluation feeding a residual (measured-minus-fitted)
// chart panel. Removed together with that panel (see the comment on
// `ThermoCpChart.tsx` for why): every stored "measured" point in this
// archive was evaluated FROM the NASA-7 polynomial itself, so a residual
// compared a curve with itself and was flat by construction, at the
// 0.0001-0.0004 level, everywhere it was checked. Nothing here computes a
// residual any more; re-add it only once a record exists whose measured
// points are independent of its own fit.
// ---------------------------------------------------------------------------

export type NasaBlock = NonNullable<ThermoRecord["nasa"]>

/** CODATA 2018 molar gas constant, J/(mol·K) -- the one place this value is
 * spelled out. Every Cp/R -> Cp conversion in this file multiplies by this
 * constant, never a rounded stand-in. */
export const GAS_CONSTANT_J_MOL_K = 8.314462618

/** Exact definition (thermochemical calorie): 1 cal = 4.184 J. */
export const JOULES_PER_CALORIE = 4.184

export type CpUnit = "j_mol_k" | "cal_mol_k"

export function cpUnitLabel(unit: CpUnit): string {
    return unit === "cal_mol_k" ? "cal/mol·K" : "J/mol·K"
}

/**
 * Display-only conversion. The STORED value is always J/mol/K (see the
 * module docstring on `ThermoCpChart.tsx` for why the archive value is
 * never re-derived from a converted display value) -- a round trip through
 * this function (convert to cal, then read the original back) must
 * reproduce the original J/mol/K number exactly, since converting FROM
 * cal is never actually performed anywhere in this app.
 */
export function convertCpForDisplay(valueJMolK: number, unit: CpUnit): number {
    return unit === "cal_mol_k" ? valueJMolK / JOULES_PER_CALORIE : valueJMolK
}

function hasFiveRealNumbers(coefficients: ReadonlyArray<number | null> | undefined): coefficients is number[] {
    return !!coefficients && coefficients.length >= 5
        && coefficients.slice(0, 5).every((value) => value !== null && Number.isFinite(value))
}

export type UsableNasaBlock = NasaBlock & { t_low: number; t_mid: number; t_high: number }

/**
 * Whether `nasa` carries everything needed to evaluate Cp anywhere in its
 * own stated range: a real, ordered boundary triplet (`t_low` < `t_mid` <
 * `t_high`) AND both five-element coefficient sets. Anything short of this
 * is treated as "no usable fit" -- honestly, never by guessing at a
 * missing boundary or a missing coefficient.
 */
export function isNasaFitUsable(nasa: NasaBlock | null | undefined): nasa is UsableNasaBlock {
    if (!nasa) return false
    const { t_low, t_mid, t_high } = nasa
    if (t_low == null || t_mid == null || t_high == null) return false
    if (!Number.isFinite(t_low) || !Number.isFinite(t_mid) || !Number.isFinite(t_high)) return false
    if (!(t_low < t_mid && t_mid < t_high)) return false
    return hasFiveRealNumbers(nasa.low_temperature_coefficients) && hasFiveRealNumbers(nasa.high_temperature_coefficients)
}

/**
 * Cp (J/mol/K) from five NASA-7 a-coefficients at one temperature -- the
 * bare polynomial, no branch selection and no validity-range check.
 * Exported on its own so the hand-checked arithmetic in
 * `thermoNasa.test.ts` exercises exactly this formula and nothing else.
 */
export function evaluateNasaCpJMolK(coefficients: readonly number[], temperatureK: number): number {
    const [a1, a2, a3, a4, a5] = coefficients
    const cpOverR = a1 + a2 * temperatureK + a3 * temperatureK ** 2 + a4 * temperatureK ** 3 + a5 * temperatureK ** 4
    return cpOverR * GAS_CONSTANT_J_MOL_K
}

export type CpCurvePoint = { temperatureK: number; cpJMolK: number }
export type CpCurve = { low: CpCurvePoint[]; high: CpCurvePoint[] }

/**
 * The smooth NASA-7 fit line, as two SEPARATE branches (`low`/`high`)
 * rather than one continuous polyline. A real NASA-7 fit is not required
 * to be perfectly continuous in value at `t_mid` (only close), and joining
 * both branches with one path would draw a diagonal line across any gap
 * instead of the honest vertical step a wrong split would actually
 * produce. Each branch evaluates `t_mid` WITH ITS OWN coefficients (the
 * low branch's last point and the high branch's first point are both
 * `t_mid`) precisely so a mis-implemented split renders as a visible step,
 * never smoothed away by which sample happens to land closest to the
 * boundary.
 *
 * `null` when the fit isn't usable at all (`isNasaFitUsable`) -- the
 * honest "no fit to draw" case; an unusable fit never produces a curve
 * with fewer points instead.
 */
export function nasaCpCurve(nasa: NasaBlock | null | undefined, pointsPerBranch = 60): CpCurve | null {
    if (!isNasaFitUsable(nasa)) return null
    const { t_low, t_mid, t_high, low_temperature_coefficients, high_temperature_coefficients } = nasa
    return {
        low: sampleBranch(low_temperature_coefficients as number[], t_low, t_mid, pointsPerBranch),
        high: sampleBranch(high_temperature_coefficients as number[], t_mid, t_high, pointsPerBranch),
    }
}

function sampleBranch(coefficients: number[], fromK: number, toK: number, pointCount: number): CpCurvePoint[] {
    const points: CpCurvePoint[] = []
    const steps = Math.max(1, pointCount - 1)
    for (let i = 0; i <= steps; i += 1) {
        const temperatureK = fromK + ((toK - fromK) * i) / steps
        points.push({ temperatureK, cpJMolK: evaluateNasaCpJMolK(coefficients, temperatureK) })
    }
    return points
}
