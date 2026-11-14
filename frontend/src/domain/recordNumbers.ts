/** One section's "Record N" numbering by ref: the position in the list the section shows (1-based). */
export type RecordNumbering = ReadonlyMap<string, number>

/** Numbers `refs` in the order given. */
export const numberRefs = (refs: readonly string[]): RecordNumbering => new Map(refs.map((ref, index) => [ref, index + 1] as const))

/** "Statmech record 2" for a record of a list of several; the plain words for the only one. */
export function numberedWords(words: string, ref: string, numbers: RecordNumbering): string {
    const position = numbers.get(ref)
    return numbers.size > 1 && position ? `${words} ${position}` : words
}
