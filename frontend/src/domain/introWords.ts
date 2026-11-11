import { isValidElement, type ReactNode } from "react"

/** A section intro is one short sentence: at most this many words. */
export const INTRO_WORD_LIMIT = 15

/** The words an intro renders, counting links and other markup by their text. */
export function introWordCount(node: ReactNode): number {
    if (node == null || typeof node === "boolean") return 0
    if (typeof node === "string" || typeof node === "number") return String(node).split(/\s+/).filter((word) => /[A-Za-z0-9]/.test(word)).length
    if (Array.isArray(node)) return node.reduce((sum: number, child: ReactNode) => sum + introWordCount(child), 0)
    if (isValidElement<{ children?: ReactNode }>(node)) return introWordCount(node.props.children)
    return 0
}
