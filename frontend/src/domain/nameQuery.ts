import { classifyIdentifier, looksLikeReferenceAttempt, publicRefPrefixes } from "./recordModel"

export const NAME_QUERY_MIN_LENGTH = 2
export const NAME_QUERY_MAX_LENGTH = 64

/** Typographic apostrophes and primes the archive folds to ' (as its own name normalisation does). */
const APOSTROPHES = /[\u2018\u2019\u201a\u201b\u02bc\u02b9\u2032\u02c8`\u00b4\uff07]/g
const NAME_CHARACTERS = /^[\p{L}\p{M}\p{N} \-,'()[\]+.:]+$/u
const SMILES_TOKEN = /^(?:Cl|Br|[BCNOPSFI]|[bcnops]|\[[^\]]*\]|[-=#$:/\\.+()@%\d])/

/** The name-search query rule: 2 to 64 letters, digits, spaces and - , ' ( ) [ ] + . :, with a letter or digit. */
export function isValidNameQuery(text: string): boolean {
    const value = text.replace(APOSTROPHES, "'").trim()
    if (value.length < NAME_QUERY_MIN_LENGTH || value.length > NAME_QUERY_MAX_LENGTH) return false
    return NAME_CHARACTERS.test(value) && /[\p{L}\p{N}]/u.test(value)
}

/** The key the session cache and the request use: lower case, single spaces. */
export function nameQueryKey(text: string): string {
    return text.replace(APOSTROPHES, "'").normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase()
}

/** True for text that is only the start of a reference prefix: "r", "sp", "rxn", "rxe_". */
function startsOfRefPrefix(value: string): boolean {
    const lower = value.toLowerCase()
    return publicRefPrefixes().some((prefix) => `${prefix}_`.startsWith(lower))
}

function readsAsSmiles(value: string): boolean {
    let rest = value
    while (rest.length > 0) {
        const token = SMILES_TOKEN.exec(rest)
        if (!token) return false
        rest = rest.slice(token[0].length)
    }
    return true
}

/**
 * True when the text is a name rather than a formula, SMILES, InChI, InChIKey or TCKDB reference.
 * Anything that reads as one of those goes to the exact search instead.
 */
export function looksLikeName(text: string): boolean {
    const value = text.trim()
    if (!value || looksLikeReferenceAttempt(value)) return false
    if (/^(formula|smiles):/i.test(value) || /^InChI=/i.test(value)) return false
    if (value.includes("[") || /^inch/i.test(value) || startsOfRefPrefix(value)) return false
    const classified = classifyIdentifier(value)
    if (classified.valid) {
        if (classified.identifier.kind !== "smiles") return false
    } else if (classified.ambiguousValue !== undefined) {
        return false
    }
    return !readsAsSmiles(value)
}
