import { useEffect, useId, useState } from "react"
import { Link } from "react-router-dom"
import { loadNetworkTopology, type NetworkTopology } from "../api/networkEntryApi"
import { NetworkMap } from "./NetworkMap"

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`

type Loaded = { key: string; topologies: Map<string, NetworkTopology> }

/** A small map of each pressure-dependent network that admits this reaction entry; nothing when none loads. */
export function ReactionNetworkRail({ networkRefs, reactionEntryRef }: { networkRefs: readonly string[]; reactionEntryRef: string }) {
    const headingId = useId()
    const key = networkRefs.join("|")
    const [loaded, setLoaded] = useState<Loaded | null>(null)
    useEffect(() => {
        if (networkRefs.length === 0) return
        const controller = new AbortController()
        Promise.all(networkRefs.map((ref) => loadNetworkTopology(ref, controller.signal).then((topology) => [ref, topology] as const, () => null)))
            .then((results) => setLoaded({ key, topologies: new Map(results.filter((result) => result !== null)) }))
        return () => controller.abort()
        // The refs are compared by value through `key`.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key])

    const shown = loaded?.key === key
        ? networkRefs.filter((ref) => (loaded.topologies.get(ref)?.states.length ?? 0) > 0)
        : []
    if (shown.length === 0) return null
    return (
        <div className="rail-network" role="group" aria-labelledby={headingId}>
            <h3 id={headingId} className="rel-map-heading">{shown.length === 1 ? "Pressure-dependent network" : "Pressure-dependent networks"}</h3>
            {shown.map((ref, index) => {
                const topology = loaded?.topologies.get(ref)
                if (!topology) return null
                const here = topology.channels.filter((channel) => channel.microreactions.some((micro) => micro.reaction_entry_ref === reactionEntryRef)).length
                return (
                    <div key={ref}>
                        <NetworkMap
                            compact
                            states={topology.states}
                            channels={topology.channels}
                            channelBarriers={null}
                            highlightReactionEntryRef={reactionEntryRef}
                            caption={`${here} of ${plural(topology.channels.length, "channel", "channels")} ${here === 1 ? "is" : "are"} this reaction (thick line).`}
                        />
                        <Link to={`/networks/${ref}`}>{shown.length === 1 ? "Open the network" : `Open network ${index + 1} of ${shown.length}`}</Link>
                    </div>
                )
            })}
        </div>
    )
}
