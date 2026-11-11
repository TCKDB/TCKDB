/** Words that rank a value or a record. Rigid-rotor kinds ("symmetric top") are the one wording that contains one innocently. */
const RANKING = /\b(best|top|recommended|winner|preferred|lowest|highest)\b/gi
const ROTOR_KIND = /\b(symmetric|asymmetric|spherical) top\b/gi

/** All text under a node, text nodes joined with spaces so neighbouring cells cannot fuse into one word. */
export function textOf(root: Node): string {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    const parts: string[] = []
    for (let node = walker.nextNode(); node; node = walker.nextNode()) parts.push(node.textContent ?? "")
    return parts.join(" ")
}

export function rankingWords(text: string): string[] {
    return text.replace(ROTOR_KIND, "").match(RANKING) ?? []
}
