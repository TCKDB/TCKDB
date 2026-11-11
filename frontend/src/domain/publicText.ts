// What a public page may print of a string a depositor could have typed.
//
// House rule:
// no depositor-typed text reaches the public DOM -- not as text, link text,
// a pill, a tooltip, a title, an aria-label, an SVG <text>, a data
// attribute or an element id. A depositor string can be anything, including
// offensive, misleading or written to look like TCKDB's own words.
//
// The API keeps returning these fields, in their marked places. This module
// holds the few places where the frontend has to look inside a server string
// that MIXES computed and typed parts, and keep only the computed ones.
//
// Exception: an electronic-state label and a term symbol that satisfy the strict grammar
// the API enforces on write (`electronicState.ts`) are a closed vocabulary (`T1`, `²Π`), not free text, and are
// shown so a reader can tell excited and spin states apart. Anything outside the grammar stays hidden.

import { validStateLabel, validTermSymbol } from "./electronicState"

/**
 * A term symbol, or `null` when it is not in the grammar the API enforces on write (`electronicState.ts`: the one
 * rule, shared with tckdb-schemas). A row stored before that grammar can hold any text.
 */
export function publicTermSymbol(value: string | null | undefined): string | null {
    return validTermSymbol(value)
}

/** An electronic-state label (`S1`, `T1`, `X`, `Ã`), or `null` when it is not in the grammar the API enforces on write. */
export function publicElectronicStateLabel(value: string | null | undefined): string | null {
    return validStateLabel(value)
}

/**
 * An opaque, stable handle for a depositor-typed key (a channel key), safe to
 * put in a `data-*` attribute or an element id. Not reversible, and carries
 * none of the original characters.
 */
export function opaqueHandle(value: string): string {
    let hash = 0x811c9dc5
    for (let index = 0; index < value.length; index += 1) {
        hash ^= value.charCodeAt(index)
        hash = Math.imul(hash, 0x01000193) >>> 0
    }
    return `k${hash.toString(16).padStart(8, "0")}`
}

/** `opaqueHandle` for an optional key; `undefined` (no attribute) when there is none. */
export function channelHandle(key: string | null | undefined): string | undefined {
    return key == null ? undefined : opaqueHandle(key)
}

const ELEMENTS = new Set((
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
    + "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu "
    + "Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr"
).split(" "))

// An element or bond key as a parameter table prints it: `C`, `C-H`, `C=O`, `N#N`, `C-Cl2`.
const KEY_PART = /^([A-Z][a-z]?)(\d*)$/

/**
 * A correction-parameter key (element, bond, component target) only when every
 * part is a real element symbol (optionally followed by a count) joined by bond
 * characters; otherwise `"unrecognised key"`. The keys are stored as typed.
 */
export function publicChemistryKey(value: string): string {
    const parts = value.split(/[-=#:~]/)
    // An element, or a two-part bond key (`C-H`, `C=O`, `N#N`): the shapes the Petersson and Melius schemes use.
    const ok = parts.length >= 1 && parts.length <= 2 && parts.every((part) => {
        const match = KEY_PART.exec(part)
        return match !== null && ELEMENTS.has(match[1])
    })
    return ok ? value : "unrecognised key"
}

const NUMERIC = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/
const BOOLEAN = /^(?:true|false)$/i

/**
 * A parameter value for a recognised key: printed only when it is a number or
 * a boolean. `canonical_value` is stored as the producer wrote it, so any other
 * string is shown as "recorded". The frontend is not served each key's closed
 * set of enum tokens, so enum-valued settings read "recorded" too.
 */
export function publicParameterValue(value: string | null | undefined): string {
    if (value == null) return "recorded"
    const trimmed = value.trim()
    return NUMERIC.test(trimmed) || BOOLEAN.test(trimmed) ? trimmed : "recorded"
}

// The closed set of Schoenflies point groups, with the spellings the parsers and
// importers emit for the linear groups (`Cinfv`, `C*v`, `C∞v`).
const POINT_GROUP = /^(?:C1|Cs|Ci|C[2-9]\d?[vh]?|D[2-9]\d?[dh]?|S[2-9]\d?|T[dh]?|O[h]?|I[h]?|C(?:inf|\*|∞)v|D(?:inf|\*|∞)h)$/

/** A Schoenflies point group symbol, or `null` for any other string. */
export function publicPointGroup(value: string | null | undefined): string | null {
    if (!value) return null
    const trimmed = value.trim()
    return POINT_GROUP.test(trimmed) ? trimmed : null
}
