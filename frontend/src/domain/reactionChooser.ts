/** What the title and equation above the entry table are read from; nothing is said when there is only one entry. */
export function chooserHeaderSourceNote(entryRef: string, total: number): string | null {
    if (total <= 1) return null
    return `Title from ${entryRef}, the first entry; entries can differ.`
}
