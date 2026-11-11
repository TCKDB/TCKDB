import { useRef } from "react"
import { Link } from "react-router-dom"
import { loadGeometryAtoms } from "../api/geometryApi"
import { loadTransitionStateEntry } from "../api/transitionStateEntryApi"
import { stageGeometryRef } from "../domain/transitionStateStage"
import { useLoaded } from "../hooks/useLoaded"
import { useNearViewport } from "../hooks/useNeighbours"
import { ViewerStage } from "./ViewerStage"

/**
 * A picture of one transition-state entry's geometry, the interactive 3D view on request. Reads the entry and its
 * geometry only once the card is near the viewport, so a page with several transition states does not read them all.
 */
export function TransitionStateStage({ transitionStateEntryRef }: { transitionStateEntryRef: string }) {
    const ref = useRef<HTMLDivElement | null>(null)
    const near = useNearViewport(ref)
    const entry = useLoaded(loadTransitionStateEntry, near ? transitionStateEntryRef : null)
    const geometryRef = entry.status === "ready" ? stageGeometryRef(entry.data) : null
    const geometry = useLoaded(loadGeometryAtoms, geometryRef)
    const atoms = geometry.status === "ready" ? geometry.data.atoms ?? [] : []
    const waiting = !near || entry.status === "loading" || entry.status === "idle" || (geometryRef !== null && (geometry.status === "loading" || geometry.status === "idle"))
    return (
        <div className="ts-stage" ref={ref}>
            {waiting && <p className="note" role="status" aria-busy="true">Loading the geometry of this transition state.</p>}
            {!waiting && atoms.length > 0 && geometryRef && (
                <>
                    <ViewerStage key={geometryRef} atoms={atoms} formula="transition state" xyzText={geometry.status === "ready" ? geometry.data.xyz_text ?? null : null} />
                    <p className="note">
                        Drawn from <Link to={`/geometries/${geometryRef}`}><code className="t-preserve-case">{geometryRef}</code></Link>, the first final geometry deposited for this entry.
                    </p>
                </>
            )}
            {!waiting && atoms.length === 0 && <p className="empty-projection">No picture: no final geometry could be read for this transition state.</p>}
        </div>
    )
}
