import { useEffect, useState } from "react"
import { ScientificRateLimitError } from "../api/scientificTransport"
import { cachedNameSearch, nameSearchBackoffSeconds, searchSpeciesNames, type NameMatch } from "../api/nameSearchApi"
import { isValidNameQuery, nameQueryKey } from "../domain/nameQuery"

export const SUGGESTION_DEBOUNCE_MS = 300
export const SUGGESTION_LIMIT = 8

export type NameSuggestions =
    | { status: "idle" | "loading" | "error"; matches: NameMatch[]; retryInSeconds: 0 }
    | { status: "ready"; matches: NameMatch[]; retryInSeconds: 0 }
    | { status: "limited"; matches: NameMatch[]; retryInSeconds: number }

type Settled = { key: string; matches: NameMatch[]; failed: boolean }

/**
 * Name suggestions for the typed text: debounced, validated with the name-search rule before any
 * request, cancelled when the text changes, cached for the session, and paused after a 429.
 */
export function useNameSuggestions(text: string, active: boolean): NameSuggestions {
    const key = nameQueryKey(text)
    const valid = active && isValidNameQuery(text)
    const [settled, setSettled] = useState<Settled | null>(null)
    const [now, setNow] = useState(() => Date.now())
    const [tick, setTick] = useState(0)
    const backoff = valid ? nameSearchBackoffSeconds(now) : 0
    const paused = backoff > 0

    useEffect(() => {
        if (!valid || paused) return
        const hit = cachedNameSearch(text, "prefix")
        if (hit) {
            setSettled({ key, matches: hit.matches, failed: false })
            return
        }
        const controller = new AbortController()
        const timer = setTimeout(() => {
            searchSpeciesNames(text, "prefix", SUGGESTION_LIMIT, controller.signal).then((result) => {
                if (controller.signal.aborted) return
                setSettled({ key, matches: result.matches, failed: false })
            }, (error: unknown) => {
                if (controller.signal.aborted) return
                if (error instanceof ScientificRateLimitError) { setNow(Date.now()); setTick((value) => value + 1); return }
                setSettled({ key, matches: [], failed: true })
            })
        }, SUGGESTION_DEBOUNCE_MS)
        return () => { clearTimeout(timer); controller.abort() }
        // `text` is covered by `key`.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key, valid, paused, tick])

    useEffect(() => {
        if (!valid || !paused) return
        const timer = setInterval(() => setNow(Date.now()), 1000)
        return () => clearInterval(timer)
    }, [valid, paused])

    if (!valid) return { status: "idle", matches: [], retryInSeconds: 0 }
    if (paused) return { status: "limited", matches: [], retryInSeconds: backoff }
    if (settled && settled.key === key) {
        if (settled.failed) return { status: "error", matches: [], retryInSeconds: 0 }
        return { status: "ready", matches: settled.matches, retryInSeconds: 0 }
    }
    return { status: "loading", matches: [], retryInSeconds: 0 }
}
