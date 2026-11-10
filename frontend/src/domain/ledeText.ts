declare const ledeBrand: unique symbol
declare const wordBrand: unique symbol

/** A sentence for the line under a record's title. Only `ledeText` makes one, so a depositor's string cannot become one. */
export type LedeText = string & { readonly [ledeBrand]: true }

/** TCKDB's own wording for a code. Only `ledeWord` makes one. */
export type LedeWord = string & { readonly [wordBrand]: true }

/** The TCKDB wording for a code; a code with no wording reads "not recorded", never the code itself. */
export function ledeWord(code: string | null | undefined, wording: Readonly<Record<string, string>>): LedeWord {
    const word = code != null && Object.hasOwn(wording, code) ? wording[code] : "not recorded"
    return word as LedeWord
}

/** Builds a lede from fixed text and, between it, only numbers and `ledeWord`s: ledeText`${3} calculations`. */
export function ledeText(strings: TemplateStringsArray, ...values: readonly (number | LedeWord)[]): LedeText {
    return strings.reduce((text, part, index) => text + part + (index < values.length ? String(values[index]) : ""), "") as LedeText
}
