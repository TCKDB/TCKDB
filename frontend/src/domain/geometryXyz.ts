import type { GeometryAtom } from "../api/geometryApi"

/**
 * The two coordinate-display units this app offers on a geometry page —
 * shared between `GeometryDetailPage` (the Å/bohr toggle on the
 * coordinate table) and `GeometryViewer` (measured distances follow the
 * same toggle; see that component's module docstring for why). Exported
 * from here, not declared locally in either consumer, so there is exactly
 * one name for "which unit a reader is currently looking at" rather than
 * two structurally-identical types that happen to agree.
 */
export type CoordinateUnitMode = "angstrom" | "bohr"

/**
 * Builds a minimal XYZ-format block from a geometry's own `atoms` rows —
 * the same rows the coordinate table renders — for the (expected-rare)
 * case where the archive did not return a ready-made `xyz_text`.
 * `GeometryViewer` only calls this as a fallback: the archive's own
 * `xyz_text` is passed to 3Dmol unmodified whenever it is present, so
 * this function is never on the path a normal deployment takes.
 *
 * Kept as a small, independently-testable pure function in its own
 * (non-component) module rather than inlined in `GeometryViewer.tsx` —
 * partly so `eslint-plugin-react-refresh`'s
 * `only-export-components` rule does not fire on a `.tsx` file
 * exporting something that is not a component, and partly because this
 * is exactly the kind of "recompute the same data a second way" spot
 * where a transcription bug (a swapped field, a dropped atom) can make
 * a rendered structure and the raw-XYZ block disagree about the same
 * geometry without either one individually looking wrong — see
 * `geometryXyz.test.ts` for a test that pins the exact output.
 *
 * Template-literal interpolation stringifies a `-0` coordinate as `"0"`
 * (`` `${-0}` === "0" ``, standard JS number-to-string behaviour) — noted
 * deliberately, not fixed: `-0` and `0` are the same physical position,
 * so this is not a data-loss bug.
 */
export function buildXyzBlock(atoms: GeometryAtom[]): string {
    const lines = atoms.map((atom) => `${atom.element} ${atom.x} ${atom.y} ${atom.z}`)
    return `${atoms.length}\n\n${lines.join("\n")}`
}

/**
 * CODATA 2018 recommended value for the Bohr radius, in ångström
 * (https://physics.nist.gov/cgi-bin/cuu/Value?bohrrada0 — 0.529177210903 Å).
 * `ANGSTROM_TO_BOHR` is derived from this as its reciprocal, rather than
 * being a second, independently-rounded literal, so the two numbers can
 * never silently drift apart.
 */
export const BOHR_RADIUS_ANGSTROM = 0.529177210903
export const ANGSTROM_TO_BOHR = 1 / BOHR_RADIUS_ANGSTROM

/**
 * Converts one ångström-valued Cartesian coordinate to bohr for display.
 * `coordinate_units` on the wire is `Literal["angstrom"]` (see
 * `api/geometryApi.ts`) — this never changes what is stored or requested,
 * only what a reader is shown; callers must not use this to imply the
 * archive holds a bohr-valued record anywhere.
 */
export function angstromToBohr(valueAngstrom: number): number {
    return valueAngstrom * ANGSTROM_TO_BOHR
}

/**
 * IUPAC element symbols to atomic number, H (1) through Og (118), keyed
 * exactly as this archive's `element` field is spelled (e.g. "Na", not
 * "NA" or "na"). Deliberately exhaustive rather than "the elements we've
 * seen so far" — an unrecognised symbol is a real possibility (a typo, a
 * dummy/ghost atom label, an isotope suffix) and must be handled by the
 * caller via `atomicNumberForSymbol`'s `null`, never by a lookup table
 * that happens to be too small to notice.
 */
export const ELEMENT_ATOMIC_NUMBERS: Record<string, number> = {
    H: 1, He: 2, Li: 3, Be: 4, B: 5, C: 6, N: 7, O: 8, F: 9, Ne: 10,
    Na: 11, Mg: 12, Al: 13, Si: 14, P: 15, S: 16, Cl: 17, Ar: 18,
    K: 19, Ca: 20, Sc: 21, Ti: 22, V: 23, Cr: 24, Mn: 25, Fe: 26, Co: 27, Ni: 28, Cu: 29, Zn: 30,
    Ga: 31, Ge: 32, As: 33, Se: 34, Br: 35, Kr: 36,
    Rb: 37, Sr: 38, Y: 39, Zr: 40, Nb: 41, Mo: 42, Tc: 43, Ru: 44, Rh: 45, Pd: 46, Ag: 47, Cd: 48,
    In: 49, Sn: 50, Sb: 51, Te: 52, I: 53, Xe: 54,
    Cs: 55, Ba: 56, La: 57, Ce: 58, Pr: 59, Nd: 60, Pm: 61, Sm: 62, Eu: 63, Gd: 64, Tb: 65, Dy: 66,
    Ho: 67, Er: 68, Tm: 69, Yb: 70, Lu: 71,
    Hf: 72, Ta: 73, W: 74, Re: 75, Os: 76, Ir: 77, Pt: 78, Au: 79, Hg: 80,
    Tl: 81, Pb: 82, Bi: 83, Po: 84, At: 85, Rn: 86,
    Fr: 87, Ra: 88, Ac: 89, Th: 90, Pa: 91, U: 92, Np: 93, Pu: 94, Am: 95, Cm: 96, Bk: 97, Cf: 98,
    Es: 99, Fm: 100, Md: 101, No: 102, Lr: 103,
    Rf: 104, Db: 105, Sg: 106, Bh: 107, Hs: 108, Mt: 109, Ds: 110, Rg: 111, Cn: 112,
    Nh: 113, Fl: 114, Mc: 115, Lv: 116, Ts: 117, Og: 118,
}

/**
 * Looks up an atomic number by element symbol. Returns `null` — never
 * `0`, which is not a valid atomic number and would read as a real (if
 * wrong) answer — for a symbol this table does not recognise, so a caller
 * can render an honest "unknown" instead.
 *
 * `Object.hasOwn`, not a plain `ELEMENT_ATOMIC_NUMBERS[symbol] ?? null`
 * index: `ELEMENT_ATOMIC_NUMBERS` is a plain object literal, so it
 * inherits `Object.prototype`, and `symbol` values like `"constructor"`
 * or `"toString"` resolve to an inherited function rather than
 * `undefined` — the old form returned that function (not `null`) for
 * such a symbol, silently breaking the declared `number | null` return
 * type. Unreachable from real chemistry data (no element is spelled
 * "constructor"), but this docstring specifically promises unknown
 * symbols are handled via `null`, so the promise should hold even for an
 * adversarial or corrupted input.
 */
export function atomicNumberForSymbol(symbol: string): number | null {
    return Object.hasOwn(ELEMENT_ATOMIC_NUMBERS, symbol) ? ELEMENT_ATOMIC_NUMBERS[symbol] : null
}
