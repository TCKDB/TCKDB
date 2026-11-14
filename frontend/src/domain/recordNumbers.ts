/** One section's "Record N" numbering by ref: the position in the list the section shows (1-based). */
export type RecordNumbering = ReadonlyMap<string, number>

/** Numbers `refs` in the order given; `offset` is how many records of the list come before this page. */
export const numberRefs = (refs: readonly string[], offset = 0): RecordNumbering => new Map(refs.map((ref, index) => [ref, offset + index + 1] as const))

/** "Statmech record 2" for a record of a list of several; the plain words for the only one (position 1 of a list of one). */
export function numberedWords(words: string, ref: string, numbers: RecordNumbering): string {
    const position = numbers.get(ref)
    return position && (numbers.size > 1 || position > 1) ? `${words} ${position}` : words
}
