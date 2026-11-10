const LINE_CHARS = 21

/** Breaks a word longer than a line, preferably after a bracket, "=", ",", "." or "-", else at the line width. */
function splitLongWord(word: string): string[] {
    const parts: string[] = []
    let rest = word
    while (rest.length > LINE_CHARS) {
        const window = rest.slice(0, LINE_CHARS)
        const at = Math.max(...[...window].map((ch, i) => ("()=,.-".includes(ch) ? i : -1)))
        const cut = at >= LINE_CHARS / 2 ? at + 1 : LINE_CHARS
        parts.push(rest.slice(0, cut))
        rest = rest.slice(cut)
    }
    return rest ? [...parts, rest] : parts
}

/** Splits a label at word boundaries, and a word too long for one line at a sensible character. */
export function wrapLabel(label: string): string[] {
    const lines: string[] = []
    for (const word of label.split(" ")) {
        const last = lines[lines.length - 1]
        if (last !== undefined && `${last} ${word}`.length <= LINE_CHARS) lines[lines.length - 1] = `${last} ${word}`
        else lines.push(...splitLongWord(word))
    }
    return lines
}

export { LINE_CHARS }
