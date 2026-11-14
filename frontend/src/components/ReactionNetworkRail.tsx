import { useEffect, useId, useState } from "react"
import { Link } from "react-router-dom"
import { loadNetworkTopology, type NetworkTopology } from "../api/networkEntryApi"
import { NetworkMap } from "./NetworkMap"

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`

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
        <div>
            <NetworkMap
                compact
                states={topology.states}
                channels={topology.channels}
                channelBarriers={null}
                highlightReactionEntryRef={reactionEntryRef}
                caption={`${plural(topology.states.length, "state", "states")}, ${plural(topology.channels.length, "channel", "channels")}. Thick lines are this reaction.`}
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
        <div className="rail-network" role="group" aria-labelledby={headingId}>
            <h3 id={headingId} className="rel-map-heading">Pressure-dependent network</h3>
            {networkRefs.map((networkRef) => <NetworkRailItem key={networkRef} networkRef={networkRef} reactionEntryRef={reactionEntryRef} />)}
        </div>
    )
}
