import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import { loadGeometryAtoms } from "../api/geometryApi"
import { shapeSummary } from "../domain/geometryShape"
import { useLoaded } from "../hooks/useLoaded"
import { ViewerStage } from "./ViewerStage"

/**
 * The picture of a conformer group or observation, with the interactive 3D view on request, and under it one sentence
 * about the shape worked out from the stored coordinates. `drawnFrom` says which stored geometry the picture is.
 */
export function ConformerStage({ geometryRef, formula, drawnFrom }: {
    geometryRef: string | null
    formula: string
    drawnFrom: ReactNode
}) {
    const geometry = useLoaded(loadGeometryAtoms, geometryRef)
    const atoms = geometry.status === "ready" ? geometry.data.atoms ?? [] : []
    const summary = shapeSummary(atoms)
    return (
        <figure className="viewer-figure" aria-label="Picture of this conformer">
            {geometryRef === null && <p className="empty-projection">No picture: no geometry is stored here.</p>}
            {geometryRef !== null && (geometry.status === "loading" || geometry.status === "idle") && (
                <p className="note" role="status" aria-busy="true">Loading the geometry.</p>
            )}
            {(geometry.status === "failed" || geometry.status === "rate-limited" || (geometry.status === "ready" && atoms.length === 0)) && (
                <p className="empty-projection">No picture: the geometry could not be read.</p>
            )}
            {geometry.status === "ready" && atoms.length > 0 && (
                <>
                    <ViewerStage key={geometryRef} atoms={atoms} formula={formula} xyzText={geometry.data.xyz_text ?? null} />
                    <figcaption>
                        {summary} {drawnFrom}{" "}
                        <Link to={`/geometries/${geometryRef}`}>Open the geometry</Link>.
                    </figcaption>
                </>
            )}
        </figure>
    )
}
