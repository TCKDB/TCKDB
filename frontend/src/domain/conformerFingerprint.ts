import type { ConformerGroupFingerprint } from "../api/speciesEntryApi"

/**
 * Turns a conformer group's numeric fingerprint into what a reader on the
 * species-entry page's conformer picker actually needs: what THIS basin
 * is, numerically, and -- when the entry has more than one group sharing
 * rotors -- how it differs from its siblings. Directly answers the
 * owner's report: "here is group 1 and 2 and then they see text about
 * what a group is so to speak but not what exactly group 1 or 2 is
 * numerically."
 *
 * Three things this module is careful never to get wrong, all corrected
 * from an earlier draft against the owner's own read of it and a fresh
 * measurement of the live archive:
 *
 * - **Never render the bin index.** `quantized_bin` is an internal
 *   integer with no meaning to a reader -- the owner: "I just think
 *   showing bin 23, 3 makes no sense to the user right?" -- in the same
 *   category as `fingerprint_hash`. What a reader can actually use is the
 *   DEGREE RANGE the bin denotes (`quantizedBin * binWidthDeg` to
 *   `(quantizedBin + 1) * binWidthDeg`), derived from `bin_width_deg`
 *   rather than a hardcoded width, since it is stored per fingerprint and
 *   a future assignment scheme may use a different one. `quantizedBin`
 *   itself is kept only internally, never placed on a rendered view type.
 * - **The bin is computed on the FOLDED angle** (see
 *   `backend/app/chemistry/torsion_fingerprint.py`'s `bin_idx = int(folded
 *   / bin_width_deg) % ...`), so `binRangeDeg` is a range in FOLDED
 *   coordinates. Measured: 6 of 66 groups have `raw_torsions_deg`
 *   differing from `folded_torsions_deg`. Every view here carries an
 *   `isFolded` flag for exactly those rotors, so a caller can say the
 *   range is in folded coordinates rather than silently presenting it as
 *   if it applied to the raw measured angle.
 * - **A rotor count of zero is the MAJORITY shape**, not an edge case:
 *   measured, 37 of 66 groups have no rotors at all (a rigid molecule
 *   with no rotatable bonds). `buildBasinRotors` returns `[]` for those,
 *   and callers must render that as a positive statement ("no rotatable
 *   bonds -- a single rigid conformer") rather than an empty list or a
 *   bare dash that reads as missing data.
 *
 * `rotor_key` (e.g. `"R_8_10"`) is a canonical `R_<atom index>_<atom
 * index>` label for the rotatable bond -- decoded here into a plain
 * "atoms 8-10" phrase (the two indices the key already encodes, nothing
 * invented) so a reader is never shown a bare, unexplained token.
 */

const ROTOR_KEY_PATTERN = /^R_(\d+)_(\d+)$/

export type RotorAtomPair = { atomA: number; atomB: number }

/** Decodes `"R_8_10"` into `{ atomA: 8, atomB: 10 }`. `null` for anything
 * that doesn't match the archive's own canonical-key shape -- this never
 * guesses at a pair from a key it can't parse. */
export function parseRotorKey(rotorKey: string): RotorAtomPair | null {
    const match = rotorKey.match(ROTOR_KEY_PATTERN)
    if (!match) return null
    return { atomA: Number(match[1]), atomB: Number(match[2]) }
}

/** A readable phrase for a rotor key. Falls back to the raw key itself
 * (never blank, never silently dropped) when it doesn't parse. */
export function rotorBondLabel(rotorKey: string): string {
    const pair = parseRotorKey(rotorKey)
    return pair ? `atoms ${pair.atomA}–${pair.atomB}` : rotorKey
}

/** The [low, high) degree range a quantized bin covers, given the
 * fingerprint's own bin width -- FOLDED coordinates, since that is what
 * the bin index was computed from. Pure arithmetic on two numbers the
 * fingerprint already carries -- never a hardcoded width. */
export function basinRangeDeg(quantizedBin: number, binWidthDeg: number): [number, number] {
    const low = quantizedBin * binWidthDeg
    return [low, low + binWidthDeg]
}

function roundDeg(value: number): number {
    // One decimal place: raw archive values carry more precision
    // (359.9994) than is useful to a reader distinguishing basins 15deg
    // apart; rounds for display only, never mutates what's compared.
    return Math.round(value * 10) / 10
}

export function formatDeg(value: number): string {
    return `${roundDeg(value)}°`
}

/** `[345, 360]` -> `"345–360°"` -- one trailing degree sign for the pair,
 * not one per number. */
export function formatRangeDeg([low, high]: [number, number]): string {
    return `${roundDeg(low)}–${roundDeg(high)}°`
}

export type BasinRotorView = {
    rotorKey: string
    bondLabel: string
    /** Folded-coordinate range: see this module's docstring. Never paired
     * with a rendered bin index -- the index itself carries no meaning to
     * a reader and must not appear in any UI text built from this view. */
    binRangeDeg: [number, number]
    /** True when symmetry folding actually moved this rotor's angle --
     * `binRangeDeg` is always in folded coordinates, but this only
     * matters to call out when folding changed something. A caller
     * should visibly say "folded coordinates" (or similar) whenever this
     * is true, rather than presenting the range as if it applied to the
     * raw measured angle directly. */
    isFolded: boolean
    representativeRawDeg: number
    /** Only set when `isFolded` -- the same rotor's folded angle, the one
     * actually used to compute `binRangeDeg`. */
    representativeFoldedDeg: number | null
}

/** This group's own numeric basin identity, one row per rotor, in the
 * fingerprint's own (already rotor-paired) order. Returns `[]` for the
 * majority case (37 of 66 groups, measured) of a rigid molecule with no
 * rotatable bonds at all -- callers must render that state explicitly,
 * not as an empty or missing section. */
export function buildBasinRotors(fingerprint: ConformerGroupFingerprint): BasinRotorView[] {
    return fingerprint.torsions.map((t) => {
        const isFolded = t.folded_torsion_deg !== t.raw_torsion_deg
        return {
            rotorKey: t.rotor_key,
            bondLabel: rotorBondLabel(t.rotor_key),
            binRangeDeg: basinRangeDeg(t.quantized_bin, fingerprint.bin_width_deg),
            isFolded,
            representativeRawDeg: t.raw_torsion_deg,
            representativeFoldedDeg: isFolded ? t.folded_torsion_deg : null,
        }
    })
}

