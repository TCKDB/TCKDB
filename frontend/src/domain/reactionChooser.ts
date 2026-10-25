/** What the title and equation above the entry table are read from. Always said, never left to be assumed. */
export function chooserHeaderSourceNote(entryRef: string, total: number): string {
    if (total <= 1) return `The title and equation are read from the one deposited entry, ${entryRef}.`
    return `The title and equation are read from ${entryRef}, the first entry listed, not from every entry. `
        + "Entries under one reaction can differ (for example, in which isomer a participant is), so check each row."
}
