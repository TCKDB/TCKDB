import type { IdentifierSearch, SearchMatch } from "../api/scientificApi"

export type IdentifierClassification =
    | { valid: true; identifier: IdentifierSearch; label: string }
    | { valid: false; message: string; ambiguousValue?: string }

const inchiKeyPattern = /^[A-Z]{14}-[A-Z]{10}-[A-Z]$/
const elementSymbols = new Set([
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac",
    "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh",
    "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
])
// A public reference's shape is `{prefix}_{26 lowercase base32 chars}` for
// EVERY prefix the backend mints (`app/services/public_refs.py`'s
// `PREFIXES` -- `PUBLIC_REF_BODY_LEN = 26` there, shared by every entry).
// Matching that GENERIC shape here, rather than only the prefixes this
// frontend happens to route, is what stops a reference this app does not
// yet have a page for (or one nobody has thought to add here) from falling
// through every other branch and landing on the SMILES catch-all at the
// bottom of `classifyIdentifier` -- exactly the bug this module exists to
// fix for `rxn_`/`rxe_`/`tse_` (a valid reaction reference sent to the
// archive as a structure query, told its SMILES could not be parsed).
const anyPublicRefPattern = /^([a-z]+)_[a-z2-7]{26}$/
const anyPublicRefStartPattern = /^[a-z]+_/i

/**
 * Prefixes with a route this frontend actually serves, beyond `spc`/`spe`
 * (which keep their own dedicated `IdentifierSearch` kinds -- see below --
 * because they go through a verifying search call, not a direct
 * navigation). A match here navigates straight to the record page; the
 * page's own `RecordStatus` reports "not found" honestly if the ref does
 * not resolve, the same as clicking any other stale link into this app.
 *
 * Source of truth for the FULL prefix vocabulary is
 * `backend/app/services/public_refs.py`'s `PREFIXES` -- this is
 * deliberately a subset of it (only prefixes `App.tsx` has a `<Route>`
 * for); see `unroutedPublicRefLabels` below for the rest.
 */
const routedPublicRefPrefixes: Record<string, { label: string; path: (value: string) => string }> = {
    rxn: { label: "reaction reference", path: (value) => `/reactions/${value}` },
    rxe: { label: "reaction-entry reference", path: (value) => `/reaction-entries/${value}` },
    tse: { label: "transition-state-entry reference", path: (value) => `/transition-state-entries/${value}` },
    cg: { label: "conformer-group reference", path: (value) => `/conformer-groups/${value}` },
    co: { label: "conformer-observation reference", path: (value) => `/conformer-observations/${value}` },
    calc: { label: "calculation reference", path: (value) => `/calculations/${value}` },
    geom: { label: "geometry reference", path: (value) => `/geometries/${value}` },
}

/**
 * Every OTHER prefix `public_refs.py` mints, with no route on this
 * frontend yet. A syntactically valid reference to one of these is real
 * (the backend would resolve it) but has nowhere to land here -- so this
 * says so plainly rather than either guessing a route or falling through
 * to a structure search (the defect this module exists to fix).
 */
const unroutedPublicRefLabels: Record<string, string> = {
    lot: "level-of-theory",
    soft: "software",
    srel: "software-release",
    wft: "workflow-tool",
    wfr: "workflow-tool-release",
    lit: "literature",
    cas: "conformer-assignment-scheme",
    fsf: "frequency-scale-factor",
    ecs: "energy-correction-scheme",
    gasch: "group-additivity-scheme",
    thm: "thermo",
    kin: "kinetics",
    sm: "statmech",
    trn: "transport",
    ts: "transition-state",
    net: "network",
    nsolve: "network-solve",
    nkin: "network-kinetics",
    sub: "submission",
    rpa: "reproducibility-assessment",
    art: "calculation-artifact",
    aie: "artifact-integrity-event",
    cpol: "curation-policy",
    rel: "dataset-release",
    rsel: "release-selection",
    rman: "release-manifest",
}

/**
 * True for input SHAPED like a public-reference attempt (`{prefix}_…`),
 * whether or not it is a syntactically COMPLETE one -- the same broad
 * heuristic `classifyIdentifier` uses internally (`anyPublicRefStartPattern`)
 * to steer a malformed or unrecognized reference toward an honest "not a
 * valid reference" message instead of falling through to a structure
 * parser it was never meant for. Exported so `reactionQuery.ts` (reaction
 * mode's own equation grammar, `IdentifierSearch.tsx`) can make the
 * identical call: a value that starts like a reference is routed through
 * `classifyIdentifier` end to end regardless of which search mode is
 * active, rather than being fed to the reaction-equation grammar -- the
 * fix for a `rxe_…` ref pasted while species mode is selected (or a
 * `spc_…` ref pasted while reaction mode is selected) failing confusingly
 * instead of routing.
 */
export function looksLikeReferenceAttempt(value: string): boolean {
    return anyPublicRefStartPattern.test(value.trim())
}

function isFormula(value: string): boolean {
    const body = value.replace(/(?:[+-]\d*|\d+[+-])$/, "")
    if (!body) return false
    let position = 0
    while (position < body.length) {
        const token = /^([A-Z][a-z]?)(\d*)/.exec(body.slice(position))
        if (!token || !elementSymbols.has(token[1]) || (token[2].startsWith("0") && token[2] !== "")) return false
        position += token[0].length
    }
    return true
}

function isPlausiblyBareSmiles(value: string): boolean {
    let position = 0
    while (position < value.length) {
        const token = /^(Cl|Br|B|C|N|O|P|S|F|I)/.exec(value.slice(position))
        if (!token) return false
        position += token[0].length
    }
    return true
}

export function classifyIdentifier(input: string): IdentifierClassification {
    const value = input.trim()
    if (!value) return { valid: false, message: "Enter a SMILES, formula, public reference, InChI, or InChIKey." }
    if (/^(formula|smiles):/i.test(value)) {
        const [, kind, supplied] = /^([^:]+):(.*)$/s.exec(value) ?? []
        const body = supplied?.trim() ?? ""
        if (!body) return { valid: false, message: `Enter an identifier after ${kind}:` }
        if (kind.toLowerCase() === "formula") {
            return isFormula(body)
                ? { valid: true, identifier: { kind: "formula", value: body }, label: "formula" }
                : { valid: false, message: "That is not a valid elemental formula." }
        }
        return { valid: true, identifier: { kind: "smiles", value: body }, label: "SMILES" }
    }
    if (/^InChI=/i.test(value)) return { valid: true, identifier: { kind: "inchi", value }, label: "InChI" }
    if (inchiKeyPattern.test(value)) return { valid: true, identifier: { kind: "inchi-key", value }, label: "InChIKey" }
    const refMatch = anyPublicRefPattern.exec(value)
    if (refMatch) {
        const prefix = refMatch[1]
        if (prefix === "spc") return { valid: true, identifier: { kind: "species-ref", value }, label: "species reference" }
        if (prefix === "spe") return { valid: true, identifier: { kind: "species-entry-ref", value }, label: "species-entry reference" }
        const routed = routedPublicRefPrefixes[prefix]
        if (routed) return { valid: true, identifier: { kind: "record-ref", value, path: routed.path(value) }, label: routed.label }
        const unroutedLabel = unroutedPublicRefLabels[prefix]
        if (unroutedLabel) {
            return {
                valid: false,
                message: `“${value}” is a ${unroutedLabel} reference. This archive does not have a page for that record type yet.`,
            }
        }
        // Ref-shaped (the exact 26-char base32 body every prefix uses) but
        // the prefix itself matches nothing this app or the archive knows
        // about -- still a reference-shaped string, not a structure query.
        return { valid: false, message: `“${value}” looks like a public reference, but “${prefix}_” is not a reference prefix this archive recognizes.` }
    }
    if (anyPublicRefStartPattern.test(value)) {
        return {
            valid: false,
            message: "Public references use a recognized prefix (e.g. spc_, spe_, rxn_, rxe_, tse_) followed by 26 lowercase base32 characters (a-z, 2-7).",
        }
    }
    if (isFormula(value)) {
        if (isPlausiblyBareSmiles(value)) {
            return { valid: false, message: `“${value}” could be a SMILES or a formula. Choose how to search it.`, ambiguousValue: value }
        }
        return { valid: true, identifier: { kind: "formula", value }, label: "formula" }
    }
    if (/^[A-Z][A-Za-z]*(?:\d+)?(?:[+-]\d*|\d+[+-])?$/.test(value)) {
        return { valid: false, message: "That is not a valid elemental formula. Use smiles: if you intended a structure string." }
    }
    if (/\s/.test(value)) return { valid: false, message: "Structure strings cannot contain spaces. Use a supported exact identifier." }
    return { valid: true, identifier: { kind: "smiles", value }, label: "SMILES" }
}

export function resultPath(match: SearchMatch): string {
    return match.entryRef
        ? `/species-entries/${encodeURIComponent(match.entryRef)}`
        : `/species/${encodeURIComponent(match.speciesRef)}`
}
