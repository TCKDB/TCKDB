import { useEffect, useId, useState } from "react"
import { Link } from "react-router-dom"
import { loadNetworkTopology, type NetworkTopology } from "../api/networkEntryApi"
import { NetworkMap } from "./NetworkMap"

function NetworkRailItem({ networkRef, reactionEntryRef }: { networkRef: string; reactionEntryRef: string }) {
    const [topology, setTopology] = useState<NetworkTopology | null>(null)
    useEffect(() => {
        const controller = new AbortController()
        loadNetworkTopology(networkRef, controller.signal)
            .then(setTopology)
            .catch(() => { /* The membership table on the page still names the network. */ })
        return () => controller.abort()
    }, [networkRef])
    if (!topology || topology.states.length === 0) return null
    return (
        <div className="rail-network">
            <NetworkMap
                compact
                states={topology.states}
                channels={topology.channels}
                channelBarriers={null}
                highlightReactionEntryRef={reactionEntryRef}
                caption={`${topology.states.length} states, ${topology.channels.length} channels. This reaction's channels are the thick lines.`}
            />
            <Link to={`/networks/${networkRef}`}>Open the network</Link>
        </div>
    )
}

/** A small map of each pressure-dependent network that admits this reaction entry; nothing when there is none. */
export function ReactionNetworkRail({ networkRefs, reactionEntryRef }: { networkRefs: readonly string[]; reactionEntryRef: string }) {
    const headingId = useId()
    if (networkRefs.length === 0) return null
    return (
        <section className="rel-map" aria-labelledby={headingId}>
            <h2 id={headingId} className="rel-map-heading">Pressure-dependent network</h2>
            {networkRefs.map((networkRef) => <NetworkRailItem key={networkRef} networkRef={networkRef} reactionEntryRef={reactionEntryRef} />)}
        </section>
    )
}
