// Electronic-state labels and term symbols, as a public page may show them.
//
// Both fields are text a depositor typed, but the API accepts them only in a strict grammar (tckdb-schemas
// `electronic_state.py`, refusal codes `electronic_state_label_invalid` / `term_symbol_invalid`) and stores one
// canonical NFC spelling (`2Pi` is stored as `²Π`). That grammar is the single rule. The two sources in
// `electronicStateGrammar.json` are a copy of the ones shipped in `electronic_state_examples.json`;
// `electronicState.parity.test.ts` fails if the copy drifts and replays every shipped example through these functions.
//
// The server already serves only values that pass (a legacy row outside the grammar reads null). These functions
// check the same grammar again, so a value outside it never reaches the DOM whichever route it took. Acceptance is
// checked here; canonicalising is the server's job, so a value is shown as served (trimmed, NFC).

import grammar from "./electronicStateGrammar.json"

const LABEL = new RegExp(`^(?:${grammar.electronic_state_label.source})$`, "u")
const TERM = new RegExp(`^(?:${grammar.term_symbol.source})$`, "u")

// The exact set of characters trimmed from both ends (`trim` in the shipped examples file). Not `String.trim()`,
// which also strips U+FEFF and, unlike Python's `str.strip()`, not `\x1c`-`\x1f` or `\x85`: the two sides must agree.
const TRIM_SET = new Set(Array.from(grammar.trim))

function trimGrammarWhitespace(value: string): string {
    let start = 0
    let end = value.length
    while (start < end && TRIM_SET.has(value[start])) start += 1
    while (end > start && TRIM_SET.has(value[end - 1])) end -= 1
    return value.slice(start, end)
}

function accepted(value: string | null | undefined, pattern: RegExp, maxLength: number): string | null {
    if (typeof value !== "string") return null
    const nfc = trimGrammarWhitespace(value).normalize("NFC")
    if (nfc.length === 0 || nfc.length > maxLength) return null
    return pattern.test(nfc.normalize("NFD")) ? nfc : null
}

/** A state label (`X`, `Ã`, `A″`, `T1`) in the grammar, or `null` for anything else. */
export function validStateLabel(value: string | null | undefined): string | null {
    return accepted(value, LABEL, grammar.electronic_state_label.max_length)
}

/** A term symbol (`²Π`, `X ¹A₁`, `³P₂`) in the grammar, or `null` for anything else. */
export function validTermSymbol(value: string | null | undefined): string | null {
    return accepted(value, TERM, grammar.term_symbol.max_length)
}

const MULTIPLICITY_WORDS = ["", "singlet", "doublet", "triplet", "quartet", "quintet", "sextet", "septet", "octet", "nonet"]
const SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
const SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
const GREEK_WORDS: Record<string, string> = { Σ: "sigma", Π: "pi", Δ: "delta", Φ: "phi", Γ: "gamma" }
const MARK_WORDS: Record<string, string> = { "⁺": "plus", "⁻": "minus", "⁄": "over", "′": "prime", "″": "double prime", "°": "odd" }

/**
 * A plain-words reading of a term symbol for a screen reader: `²Π` is "doublet pi", `³Σg⁻` is "triplet sigma g
 * minus", `¹A₁` is "singlet A 1". Used as an `aria-label` beside the printed symbol. Superscript digits only ever
 * carry the multiplicity in the grammar; subscript digits are the index, J or Omega. A symbol outside the grammar
 * has no reading (`null`).
 */
export function termSymbolSpoken(value: string | null | undefined): string | null {
    const term = validTermSymbol(value)
    if (term === null) return null
    const words: string[] = []
    for (const ch of term.normalize("NFD")) {
        const superscript = SUPERSCRIPT_DIGITS.indexOf(ch)
        const subscript = SUBSCRIPT_DIGITS.indexOf(ch)
        if (superscript >= 0) words.push(MULTIPLICITY_WORDS[superscript] ?? String(superscript))
        else if (subscript >= 0) words.push(String(subscript))
        else if (ch in GREEK_WORDS) words.push(GREEK_WORDS[ch])
        else if (ch in MARK_WORDS) words.push(MARK_WORDS[ch])
        else if (ch === "̃") words.push("tilde")
        else if (ch !== " ") words.push(ch)
    }
    return words.join(" ")
}
