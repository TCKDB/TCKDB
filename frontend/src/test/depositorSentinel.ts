// Helpers for the "no depositor-typed text on a public page" guard tests (owner
// decision 2026-11-03). A page test plants a unique sentinel in EVERY field the
// depositor typed, renders the real page or component, and asserts that no
// sentinel reached the DOM -- text, attributes, ids, or SVG text.
//
// `document.body.innerHTML` covers attributes as well as text, which is the
// point: a tooltip, an aria-label or a data attribute is as public as a heading.
// (Nothing in the app sets `document.title`, so it is not asserted.)
//
// Sentinels prove no typed string is echoed verbatim. They cannot prove that a
// shape check (a regex that decides a string "looks like" chemistry) is safe, so
// the ADVERSARIAL strings below are values that pass every shape check the app
// has; a page must not turn them into TCKDB wording either.

import { expect } from "vitest"

export const DEPOSITOR_SENTINEL = "DEPOSITOR_SENTINEL"

/**
 * Strings written to look like TCKDB's own vocabulary: a stereo mark, a term
 * symbol, a point group, a SMILES, an element key. A depositor can type any of
 * them into a free-text field.
 */
export const ADVERSARIAL = {
    stereoMark: "R",
    joinedLabel: "excited R,S,R,S,E,Z",
    smilesWord: "[2H]POOP",
    unmappedSmiles: "PISS.ON.BOOBS",
    pointGroup: "PISS",
    elementKey: "Fu-Ck",
} as const

/** Words that appear on a page only if an adversarial string was turned into text. */
const ADVERSARIAL_WORDS = ["POOP", "PISS", "BOOBS", "Fu-Ck"]

export interface PlantedFixture<T> {
    /** A deep copy of the fixture with a sentinel in every typed field. */
    value: T
    /** Every sentinel planted, so a test can prove the fixture actually carried them. */
    planted: string[]
    /** The typed keys the caller asked for, and the sentinels planted under each. */
    byKey: Record<string, string[]>
    typedKeys: readonly string[]
}

let counter = 0

/**
 * Deep-copies `fixture`, replacing the value of every property whose key is in
 * `typedKeys` -- string, or null -- with `DEPOSITOR_SENTINEL_<key>_<n>`. The
 * counter keeps each sentinel unique, so a failure names the exact field.
 */
export function plantSentinels<T>(
    fixture: T,
    typedKeys: readonly string[],
    /** Subtrees left untouched: curator-authored text (`review`, `review_history`) is not depositor text. */
    skipSubtrees: readonly string[] = ["review", "review_history"],
): PlantedFixture<T> {
    const keys = new Set(typedKeys)
    const skip = new Set(skipSubtrees)
    const planted: string[] = []
    const byKey: Record<string, string[]> = Object.fromEntries(typedKeys.map((key) => [key, []]))
    const walk = (node: unknown): unknown => {
        if (Array.isArray(node)) return node.map(walk)
        if (node !== null && typeof node === "object") {
            const out: Record<string, unknown> = {}
            for (const [key, child] of Object.entries(node as Record<string, unknown>)) {
                if (skip.has(key)) {
                    out[key] = child
                } else if (keys.has(key) && (typeof child === "string" || child === null)) {
                    const sentinel = `${DEPOSITOR_SENTINEL}_${key}_${counter++}`
                    planted.push(sentinel)
                    byKey[key].push(sentinel)
                    out[key] = sentinel
                } else {
                    out[key] = walk(child)
                }
            }
            return out
        }
        return node
    }
    return { value: walk(fixture) as T, planted, byKey, typedKeys }
}

/**
 * EVERY typed key the caller listed must have been planted at least once: a key
 * the fixture does not carry would otherwise pass vacuously.
 */
export function expectPlanted(result: Pick<PlantedFixture<unknown>, "byKey" | "typedKeys" | "planted">): void {
    for (const key of result.typedKeys) {
        expect(result.byKey[key], `typed key "${key}" was never planted in the fixture`).not.toHaveLength(0)
    }
    expect(new Set(result.planted).size).toBe(result.planted.length)
}

/** No sentinel anywhere in the document markup (text and every attribute). */
export function expectNoDepositorText(): void {
    const html = document.body.innerHTML
    const leaked = html.match(new RegExp(`${DEPOSITOR_SENTINEL}_[A-Za-z0-9_]+`, "g")) ?? []
    expect(Array.from(new Set(leaked))).toEqual([])
}

/**
 * A sentinel may appear only as the text of a `DepositorNote` quote: never in an attribute, a heading,
 * a title or any other element, and never in a note's label or origin line.
 */
export function expectDepositorTextOnlyInNotes(): void {
    const clone = document.body.cloneNode(true) as HTMLElement
    clone.querySelectorAll(".depositor-note blockquote").forEach((quote) => { quote.textContent = "" })
    const leaked = clone.innerHTML.match(new RegExp(`${DEPOSITOR_SENTINEL}_[A-Za-z0-9_]+`, "g")) ?? []
    expect(Array.from(new Set(leaked))).toEqual([])
}

/**
 * Adversarial strings must not surface, and must not be turned into TCKDB
 * wording ("R enantiomer", "excited state") by a page that was given only a joined label.
 */
export function expectNoAdversarialText(options: { allowWording?: readonly string[] } = {}): void {
    const html = document.body.innerHTML
    for (const word of ADVERSARIAL_WORDS) expect(html, `adversarial "${word}" reached the DOM`).not.toContain(word)
    const text = document.body.textContent ?? ""
    for (const [name, pattern] of [["enantiomer", /enantiomer/], ["E/Z isomer", /\b[EZ] isomer/], ["allene", /\b[RS]a allene/]] as const) {
        if (options.allowWording?.includes(name)) continue
        expect(text, `stereo wording "${name}" built from an unknown-origin label`).not.toMatch(pattern)
    }
}

/** Sets a nested value by dot path (`records.0.entries.1.term_symbol`) in a planted fixture. */
export function setAt(root: unknown, path: string, newValue: unknown): void {
    const parts = path.split(".")
    let node = root as Record<string, unknown>
    for (const part of parts.slice(0, -1)) node = node[part] as Record<string, unknown>
    node[parts[parts.length - 1]] = newValue
}
