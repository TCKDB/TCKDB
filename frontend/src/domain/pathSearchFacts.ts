import type { CalculationChild } from "../api/calculationChildrenApi"
import type { PathSearchSummary } from "../api/calculationPathsApi"
import { hartreeText } from "./energyProfile"
import { whereText, type PathProfile } from "./pathProfile"

const METHOD_WORDS: Readonly<Record<string, string>> = {
    neb: "NEB (nudged elastic band)",
    gsm: "GSM (growing string)",
    growing_string: "Growing string",
    freezing_string: "Freezing string",
    other: "Another method",
}

export function methodWords(method: string): string {
    return Object.hasOwn(METHOD_WORDS, method) ? METHOD_WORDS[method] : method.replaceAll("_", " ")
}

/** The method as a short name for a compact node: "GSM", "NEB". */
export function methodShort(method: string): string {
    return method === "neb" || method === "gsm" ? method.toUpperCase() : methodWords(method)
}

export function endsWords(summary: PathSearchSummary): string {
    const ended = summary.is_double_ended == null ? null : summary.is_double_ended ? "double-ended" : "single-ended"
    const count = summary.source_endpoint_count
    const endpoints = count == null ? null : `${count} ${count === 1 ? "endpoint" : "endpoints"}`
    return [ended, endpoints].filter(Boolean).join(" · ") || "not recorded"
}

export function pointsWords(summary: PathSearchSummary): string {
    const stored = `${summary.stored_point_count} stored`
    return summary.n_points == null ? stored : `${stored} of ${summary.n_points}`
}

/** The point the algorithm picked: the result row's index, or the point flagged as the guess. */
export function guessIndex(summary: PathSearchSummary, profile: PathProfile): number | null {
    if (summary.selected_ts_point_index != null) return summary.selected_ts_point_index
    const flagged = profile.points.find((point) => point.emphasis === "guess" || point.emphasis === "guess-climbing")
    return flagged?.index ?? null
}

export function guessWords(summary: PathSearchSummary, profile: PathProfile): string {
    const index = guessIndex(summary, profile)
    if (index === null) return "not recorded"
    const point = profile.points.find((candidate) => candidate.index === index)
    if (!point) return `point ${index} (not among the stored points)`
    return point.coordinate === null ? `point ${index}` : `point ${index} · ${whereText(profile, point)}`
}

export function climbingWords(summary: PathSearchSummary, profile: PathProfile): string {
    const flagged = profile.points.find((point) => point.emphasis === "climbing" || point.emphasis === "guess-climbing")
    const index = summary.climbing_image_index ?? flagged?.index ?? null
    if (index !== null) return `point ${index}`
    return summary.method === "neb" ? "not recorded" : "none (not a NEB)"
}

export function convergedWords(value: boolean | null | undefined): string {
    if (value == null) return "not recorded"
    return value ? "yes" : "no"
}

/** A sentence for a band of which only some images were stored. */
export function partialBandNote(summary: PathSearchSummary): string | null {
    if (summary.method !== "neb" || summary.stored_point_count > 1) return null
    return summary.stored_point_count === 1
        ? "Only one image of this band was deposited, so the path is a single point."
        : "No image of this band was deposited."
}

const CHILD_ORDER = ["opt", "freq", "sp", "irc", "scan"]

export function childOrder(children: readonly CalculationChild[]): CalculationChild[] {
    const rank = (type: string) => (CHILD_ORDER.includes(type) ? CHILD_ORDER.indexOf(type) : CHILD_ORDER.length)
    return [...children].sort((a, b) => rank(a.calculation.type) - rank(b.calculation.type) || a.calculation.calculation_ref.localeCompare(b.calculation.calculation_ref))
}

const LEG_WORDS: Readonly<Record<string, string>> = { both: "both legs", forward: "forward leg", reverse: "reverse leg" }

/** What one dependent calculation found, from its own result only. */
export function childFacts(child: CalculationChild): string {
    const results = child.results
    const type = child.calculation.type
    if (type === "opt") {
        const opt = results?.opt
        const converged = opt?.converged == null ? "convergence not recorded"
            : opt.converged ? (opt.n_steps != null ? `converged in ${opt.n_steps} ${opt.n_steps === 1 ? "step" : "steps"}` : "converged") : "not converged"
        return [converged, opt?.final_energy_hartree != null ? `${hartreeText(opt.final_energy_hartree)} hartree` : null].filter(Boolean).join(" · ")
    }
    if (type === "freq") {
        const freq = results?.freq
        const imaginary = freq?.n_imag == null ? "imaginary modes not recorded" : `${freq.n_imag} imaginary`
        const value = freq?.imag_freq_cm1 != null ? `${Math.abs(freq.imag_freq_cm1).toFixed(2)}i cm⁻¹` : null
        return [imaginary, value].filter(Boolean).join(" · ")
    }
    if (type === "sp") {
        const energy = results?.sp?.electronic_energy_hartree
        return energy != null ? `${hartreeText(energy)} hartree` : "energy not recorded"
    }
    if (type === "irc") {
        const irc = results?.irc
        const legs = irc?.direction ? (Object.hasOwn(LEG_WORDS, irc.direction) ? LEG_WORDS[irc.direction] : irc.direction) : "direction not recorded"
        return [legs, irc?.point_count != null ? `${irc.point_count} points` : null].filter(Boolean).join(" · ")
    }
    if (type === "scan") {
        const scan = results?.scan
        const relaxed = scan?.is_relaxed == null ? null : scan.is_relaxed ? "relaxed" : "rigid"
        return [scan?.dimension != null ? `${scan.dimension}-D` : null, relaxed].filter(Boolean).join(" · ") || "scan"
    }
    return ""
}

/** "optimised from point 8": the edge from the path search to the calculation that took its guess. */
export function edgeWords(role: string | null, guess: number | null): string {
    if (role === "optimized_from") return guess === null ? "optimised from the picked guess" : `optimised from point ${guess}`
    return role ? role.replaceAll("_", " ") : "used by"
}
