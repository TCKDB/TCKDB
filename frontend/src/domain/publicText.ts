// What a public page may print of a string a depositor could have typed.
//
// House rule (owner decision 2026-11-03, widening the 2026-09 TS-label rule):
// no depositor-typed text reaches the public DOM -- not as text, link text,
// a pill, a tooltip, a title, an aria-label, an SVG <text>, a data
// attribute or an element id. A depositor string can be anything, including
// offensive, misleading or written to look like TCKDB's own words.
//
// The API keeps returning these fields, in their marked places. This module
// holds the few places where the frontend has to look inside a server string
// that MIXES computed and typed parts, and keep only the computed ones.

// A stereo descriptor token as the server writes it (DR-0038): one CIP mark per
// stereo element, joined by commas -- `R`, `Sa`, `E,?`, `-`, `r`.
const STEREO_ELEMENT = "(?:[RS][as]?|[EZ]|[rs-]|\\?)"
const STEREO_TOKEN = new RegExp(`^${STEREO_ELEMENT}(?:,${STEREO_ELEMENT})*$`)

// The words the server itself appends or substitutes.
const SERVER_WORDS = new Set(["partial", "unresolved", "excited", "ground", "state"])

// A term symbol in a recognisable shape: multiplicity, letter, optional g/u,
// +/- or J subscript or prime (`3Σg-`, `2P3/2`, `^3P`, `3B1`, `2A'`). Anything else is typed text.
const TERM_SYMBOL = /^[¹²³⁴⁵⁶⁷^]?[1-9]\d?[A-HSPDFIΣΠΔΦΓ](?:[_]?\d\/\d|\d|[gu]|[+\-−'′″"]){0,4}$/

// SMILES, token by token. A bracket atom is parsed (isotope, element, chirality,
// hydrogens, charge, atom class), so `[anything typed]` is not a SMILES atom.
const BRACKET_ATOM = String.raw`\[(?:\d+)?(?:[A-Z][a-z]?|[bcnops]|se|as)(?:@{1,2}(?:TH|AL|SP|TB|OH)?\d*)?(?:H\d*)?(?:[+-]{1,3}\d*)?(?::\d+)?\]`
const SMILES_ATOM = `(?:${BRACKET_ATOM}|Cl|Br|[BCNOPSFIbcnops*]|[-=#:/\\$().~@+.>]|%\\d\\d|\\d)`
const SMILES_TOKEN = new RegExp(`^${SMILES_ATOM}+$`)
const ISOTOPE_ATOM = /\[\d+[A-Z]/

/** A term symbol, or `null` when what was typed is not shaped like one. */
export function publicTermSymbol(value: string | null | undefined): string | null {
    if (!value) return null
    const trimmed = value.trim()
    return TERM_SYMBOL.test(trimmed) ? trimmed : null
}

/**
 * `species_entry_label` is built server-side from stereo, state kind,
 * `electronic_state_label`, `term_symbol` and `isotope_key`, space-joined. The
 * state label and the (unvalidated) term symbol are depositor-typed, so this
 * keeps only the tokens that are recognisably computed or in a validated
 * shape. `null` when nothing is left.
 */
export function publicSpeciesEntryLabel(label: string | null | undefined): string | null {
    if (!label) return null
    const kept = label.split(/\s+/).filter((token) => {
        if (token === "") return false
        if (SERVER_WORDS.has(token)) return true
        if (STEREO_TOKEN.test(token)) return true
        if (TERM_SYMBOL.test(token)) return true
        return SMILES_TOKEN.test(token) && ISOTOPE_ATOM.test(token)
    })
    return kept.length > 0 ? kept.join(" ") : null
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

/**
 * A network state's one-string spelling (`N=N (E) + [H][H]`), rebuilt from the
 * participants with each `species_entry_label` reduced to its computed tokens.
 * The server's own `state_label` embeds the unreduced labels, so it is only
 * used when there are no participants to rebuild from.
 */
export function publicStateLabel(composition: {
    participants?: ReadonlyArray<{ canonical_smiles: string; species_entry_label?: string | null; stoichiometry: number }>
    participants_truncated?: boolean
    state_label: string
}): string {
    const participants = composition.participants ?? []
    if (participants.length === 0) return composition.state_label
    const parts = participants.map((participant) => {
        const label = publicSpeciesEntryLabel(participant.species_entry_label)
        let term = label ? `${participant.canonical_smiles} (${label})` : participant.canonical_smiles
        if (participant.stoichiometry !== 1) term = `${participant.stoichiometry} ${term}`
        return term
    })
    if (composition.participants_truncated) parts.push("...")
    return parts.join(" + ")
}

/**
 * A SMILES string only if every character parses as SMILES (or reaction-SMILES)
 * syntax; otherwise `null`. For an unmapped SMILES the depositor supplied,
 * which the server stores as typed text and does not validate or canonicalise.
 */
export function publicSmiles(value: string | null | undefined): string | null {
    if (!value) return null
    const trimmed = value.trim()
    return trimmed !== "" && SMILES_TOKEN.test(trimmed) ? trimmed : null
}

// An element or bond key as a parameter table prints it: `C`, `C-H`, `C=O`, `N#N`, `C-Cl2`.
const CHEMISTRY_KEY = /^[A-Z][a-z]?(?:[-=#:~][A-Z][a-z]?\d*)*\d*$/

/**
 * A correction-parameter key (element, bond, component target) only when it is
 * shaped like one; otherwise `"unrecognised key"`. The keys are stored as the
 * depositor typed them.
 */
export function publicChemistryKey(value: string): string {
    return CHEMISTRY_KEY.test(value) ? value : "unrecognised key"
}
