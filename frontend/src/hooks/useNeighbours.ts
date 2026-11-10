import { useEffect, useMemo, useState, type RefObject } from "react"
import { dedupedFetch } from "../api/requestCache"
import {
    loadCalculationProbe,
    loadConformerProbe,
    loadMoleculeProbe,
    loadNetworkProbe,
    loadReactionProbe,
    type NeighbourProbe,
} from "../api/neighbourApi"
import {
    reactionEntryNodes,
    speciesEntryNodes,
    type NeighbourNode,
    type ParticipantInput,
    type SpeciesEntryProbes,
} from "../domain/neighbourNodes"

const NEAR_MARGIN = "240px"

/** True once the element is within a screen's height of the viewport, and stays true. Without IntersectionObserver it is true at once. */
export function useNearViewport(ref: RefObject<Element | null>): boolean {
    const [near, setNear] = useState(() => typeof IntersectionObserver === "undefined")
    useEffect(() => {
        const element = ref.current
        if (near || !element || typeof IntersectionObserver === "undefined") return
        const observer = new IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) {
                setNear(true)
                observer.disconnect()
            }
        }, { rootMargin: NEAR_MARGIN })
        observer.observe(element)
        return () => observer.disconnect()
    }, [ref, near])
    return near
}

type ProbeName = keyof SpeciesEntryProbes
const PROBES: { name: ProbeName; load: typeof loadMoleculeProbe; key: (entryRef: string, smiles: string | null) => string | null }[] = [
    { name: "molecule", load: loadMoleculeProbe, key: (entryRef) => entryRef },
    { name: "conformers", load: loadConformerProbe, key: (entryRef) => entryRef },
    { name: "calculations", load: loadCalculationProbe, key: (entryRef) => entryRef },
    { name: "networks", load: loadNetworkProbe, key: (entryRef) => entryRef },
    { name: "reactions", load: loadReactionProbe, key: (_entryRef, smiles) => smiles },
]

type Settled = Partial<Record<ProbeName, NeighbourProbe | null>>

export type NeighboursState = { status: "idle" | "loading" | "ready"; nodes: NeighbourNode[] }

/**
 * Neighbours of a species entry, built from five existing one-row reads. Nothing is fetched until `enabled` (the map is
 * near the viewport); shared requests are deduplicated by `dedupedFetch`. A read that fails leaves its node out.
 */
export function useSpeciesEntryNeighbours(entryRef: string, smiles: string | null, enabled: boolean): NeighboursState {
    const scope = `${entryRef}\n${smiles ?? ""}`
    const [settled, setSettled] = useState<{ scope: string; probes: Settled }>({ scope, probes: {} })

    useEffect(() => {
        if (!enabled) return
        let mounted = true
        const subscriptions = PROBES.flatMap(({ name, load, key }) => {
            const probeKey = key(entryRef, smiles)
            if (probeKey === null) return []
            const subscription = dedupedFetch(load, probeKey, (signal, onRateLimited) => load(probeKey, signal, onRateLimited))
            const finish = (value: NeighbourProbe | null) => {
                if (!mounted) return
                setSettled((current) => ({
                    scope,
                    probes: { ...(current.scope === scope ? current.probes : {}), [name]: value },
                }))
            }
            subscription.promise.then(finish, (error: unknown) => {
                if (error instanceof DOMException && error.name === "AbortError") return
                finish(null)
            })
            return [subscription]
        })
        return () => {
            mounted = false
            for (const subscription of subscriptions) subscription.unsubscribe()
        }
    }, [entryRef, smiles, scope, enabled])

    return useMemo(() => {
        if (!enabled) return { status: "idle", nodes: [] }
        const probes = settled.scope === scope ? settled.probes : {}
        const expected = PROBES.filter(({ key }) => key(entryRef, smiles) !== null).map(({ name }) => name)
        const done = expected.every((name) => name in probes)
        const failed = new Set(expected.filter((name) => name in probes && probes[name] === null))
        return {
            status: done ? "ready" : "loading",
            nodes: speciesEntryNodes(entryRef, {
                molecule: probes.molecule ?? null,
                conformers: probes.conformers ?? null,
                calculations: probes.calculations ?? null,
                networks: probes.networks ?? null,
                reactions: probes.reactions ?? null,
            }, failed),
        }
    }, [enabled, settled, scope, entryRef, smiles])
}

/** Neighbours of a reaction entry come from the page's own data; no request is made. */
export function useReactionEntryNeighbours(input: {
    reactionRef: string | null
    reactants: ParticipantInput[]
    products: ParticipantInput[]
}): NeighboursState {
    const { reactionRef, reactants, products } = input
    return useMemo(
        () => ({ status: "ready", nodes: reactionEntryNodes({ reactionRef, reactants, products }) }),
        [reactionRef, reactants, products],
    )
}
