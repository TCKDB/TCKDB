import { useRef } from "react"
import { Link } from "react-router-dom"
import { loadGeometryAtoms } from "../api/geometryApi"
import { loadTransitionStateGeometries } from "../api/transitionStateEntryApi"
import { stageGeometryRef } from "../domain/transitionStateStage"
import { useLoaded } from "../hooks/useLoaded"
import { useNearViewport } from "../hooks/useNeighbours"
import { ViewerStage } from "./ViewerStage"

/**
 * A picture of one transition-state entry's geometry, the interactive 3D view on request. Reads the entry and its
 * geometry only once the card is near the viewport, so a page with several transition states does not read them all.
 */
export function TransitionStateStage({ transitionStateEntryRef, geometryRef: known }: {
    transitionStateEntryRef: string
    /** A caller that already has the entry's final geometry ref passes it (or null when it has none), so the entry is not read again. */
    geometryRef?: string | null
}) {
    const ref = useRef<HTMLDivElement | null>(null)
    const near = useNearViewport(ref)
    const entry = useLoaded(loadTransitionStateGeometries, near && known === undefined ? transitionStateEntryRef : null)
    const geometryRef = known !== undefined ? known : entry.status === "ready" ? stageGeometryRef(entry.data) : null
    const geometry = useLoaded(loadGeometryAtoms, near ? geometryRef : null)
    const atoms = geometry.status === "ready" ? geometry.data.atoms ?? [] : []
    const loading = near && (known === undefined && (entry.status === "loading" || entry.status === "idle") || (geometryRef !== null && (geometry.status === "loading" || geometry.status === "idle")))
    return (
        <div className="ts-stage" ref={ref}>
            {!near && <p className="note">Picture loads when shown.</p>}
            {loading && <p className="note" role="status" aria-busy="true">Loading the geometry of this transition state.</p>}
            {near && !loading && atoms.length > 0 && geometryRef && (
                <>
                    <ViewerStage key={geometryRef} atoms={atoms} formula={`transition state ${transitionStateEntryRef}`} xyzText={geometry.status === "ready" ? geometry.data.xyz_text ?? null : null} />
                    <p className="note">
                        First deposited final geometry: <Link to={`/geometries/${geometryRef}`}><code className="t-preserve-case">{geometryRef}</code></Link>.
                    </p>
                </>
            )}
            {near && !loading && atoms.length === 0 && <p className="empty-projection">No picture: no final geometry could be read for this transition state.</p>}
        </div>
    )
}
