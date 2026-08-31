import { useEffect, useState } from "react"
import type { VocabEntry } from "../api/vocabApi"

/**
 * Three states for an UNSCOPED vocabulary select (method, basis, software,
 * workflow tool): still loading, loaded (however many entries -- zero is a
 * legitimate archive state, not an error), or the fetch failed. `loading`
 * and `unavailable` both render as an empty option list, but with distinct
 * copy -- see `BrowseFilterForm`'s `VocabField`.
 */
export type VocabularyState =
    | { status: "loading" }
    | { status: "ready"; entries: VocabEntry[] }
    | { status: "unavailable" }

type VocabularyResult = { loader: unknown; status: "ready"; entries: VocabEntry[] } | { loader: unknown; status: "unavailable" }

/**
 * Fetches a `/meta/*` list once per mount, and again whenever `loader`
 * itself changes identity. `loader` is usually a stable module-level
 * function reference (e.g. `loadMethods`) that takes no arguments other
 * than the abort signal, so the effect fires exactly once. A scope-varying
 * caller (e.g. `ProvenanceFields`'s `record_kind`-scoped software/workflow
 * tool loaders) instead passes a closure memoized on the scoping value
 * (`useMemo(..., [recordKind])`) -- stable across renders where the scope
 * has not changed, and a fresh reference exactly when it has, so the
 * effect refires only on a genuine scope change, not on every render. A
 * failed fetch degrades to `unavailable` rather than throwing, so one
 * broken vocabulary endpoint cannot take the rest of the filter form (or
 * the listing behind it) down with it.
 *
 * "loading" is DERIVED during render (comparing the stored result's own
 * `loader` against the current one) rather than set synchronously inside
 * the effect -- mirrors `useScientificRecord`'s `visibleState` pattern.
 * A synchronous `setState({status: "loading"})` as the first statement of
 * an effect body is a real anti-pattern here (and is what
 * `react-hooks/set-state-in-effect` flags): the state is knowable from
 * props alone the moment `loader` changes, so there is nothing to wait
 * for a render-triggered effect to tell React that isn't already true.
 */
export function useVocabulary(loader: (signal?: AbortSignal) => Promise<VocabEntry[]>): VocabularyState {
    const [result, setResult] = useState<VocabularyResult | null>(null)

    useEffect(() => {
        const controller = new AbortController()
        loader(controller.signal)
            .then((entries) => setResult({ loader, status: "ready", entries }))
            .catch((error: unknown) => {
                if (controller.signal.aborted) return
                if (error instanceof DOMException && error.name === "AbortError") return
                setResult({ loader, status: "unavailable" })
            })
        return () => controller.abort()
    }, [loader])

    if (result && result.loader === loader) return result
    return { status: "loading" }
}

/**
 * Four states for a PARENT-SCOPED version select (software version,
 * workflow tool version): no parent chosen yet, loading the chosen
 * parent's versions, loaded (an EMPTY list here is real -- most of this
 * archive's software packages have no recorded release version, see
 * `BrowseFilterForm`'s doc comment and issue #305 -- never collapsed into
 * the "no parent" or "loading" copy), or the fetch failed. Re-fetches
 * whenever `parent` changes; the caller is responsible for also CLEARING
 * the version filter's own value on that same change (see `VersionField`
 * in `BrowseFilterForm`) -- this hook only owns the vocabulary, not the
 * selection.
 */
export type VersionVocabularyState =
    | { status: "no-parent" }
    | { status: "loading" }
    | { status: "ready"; entries: VocabEntry[] }
    | { status: "unavailable" }

type VersionVocabularyResult =
    | { parent: string; status: "ready"; entries: VocabEntry[] }
    | { parent: string; status: "unavailable" }

export function useVersionVocabulary(
    parent: string,
    loader: (parent: string, signal?: AbortSignal) => Promise<VocabEntry[]>,
): VersionVocabularyState {
    const [result, setResult] = useState<VersionVocabularyResult | null>(null)

    useEffect(() => {
        if (parent === "") return // "no-parent" is derived below -- nothing to fetch, nothing to store.
        const controller = new AbortController()
        loader(parent, controller.signal)
            .then((entries) => setResult({ parent, status: "ready", entries }))
            .catch((error: unknown) => {
                if (controller.signal.aborted) return
                if (error instanceof DOMException && error.name === "AbortError") return
                setResult({ parent, status: "unavailable" })
            })
        return () => controller.abort()
    }, [parent, loader])

    if (parent === "") return { status: "no-parent" }
    if (result && result.parent === parent) return result
    return { status: "loading" }
}
