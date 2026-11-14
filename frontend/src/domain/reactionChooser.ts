/** What the title and equation above the entry table are read from; nothing is said when there is only one version. */
export function chooserHeaderSourceNote(total: number): string | null {
    if (total <= 1) return null
    return "Title from Version 1; versions can differ."
}
