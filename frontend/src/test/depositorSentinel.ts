// Helpers for the "no depositor-typed text on a public page" guard tests (owner
// decision 2026-11-03). A page test plants a unique sentinel in EVERY field the
// depositor typed, renders the real page or component, and asserts that no
// sentinel reached the DOM -- text, attributes, ids, SVG text, or the title.
//
// `document.body.innerHTML` covers attributes as well as text, which is the
// point: a tooltip, an aria-label or a data attribute is as public as a heading.

import { expect } from "vitest"

export const DEPOSITOR_SENTINEL = "DEPOSITOR_SENTINEL"

export interface PlantedFixture<T> {
    /** A deep copy of the fixture with a sentinel in every typed field. */
    value: T
    /** Every sentinel planted, so a test can prove the fixture actually carried them. */
    planted: string[]
}

let counter = 0

/**
 * Deep-copies `fixture`, replacing the value of every property whose key is in
 * `typedKeys` -- string, or null/absent-but-present -- with
 * `DEPOSITOR_SENTINEL_<key>_<n>`. The counter keeps each sentinel unique, so a
 * failure names the exact field that leaked.
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
                    out[key] = sentinel
                } else {
                    out[key] = walk(child)
                }
            }
            return out
        }
        return node
    }
    return { value: walk(fixture) as T, planted }
}

/** The fixture must have carried sentinels, or the assertion below proves nothing. */
export function expectPlanted(planted: readonly string[], atLeast = 1): void {
    expect(planted.length).toBeGreaterThanOrEqual(atLeast)
    expect(new Set(planted).size).toBe(planted.length)
}

/** No sentinel anywhere in the document -- markup (so attributes), or title. */
export function expectNoDepositorText(): void {
    const html = document.body.innerHTML
    const leaked = html.match(new RegExp(`${DEPOSITOR_SENTINEL}_[A-Za-z0-9_]+`, "g")) ?? []
    expect(Array.from(new Set(leaked))).toEqual([])
    expect(document.title).not.toContain(DEPOSITOR_SENTINEL)
}
