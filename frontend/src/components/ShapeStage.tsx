import { Link } from "react-router-dom"
import { loadGeometry } from "../api/geometryApi"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel } from "../domain/conformerEvidence"
import { pictureGeometryRef } from "../domain/conformerGallery"
import { useLoaded } from "../hooks/useLoaded"
import { SectionHeading } from "./PageSections"
import { ViewerStage } from "./ViewerStage"

/** The selected shape's picture, with the interactive 3D view on request. At most one live viewer per page. */
export function ShapeStage({ conformer, formula }: { conformer: ConformerProjection; formula: string }) {
    const geometryRef = pictureGeometryRef(conformer)
    const geometry = useLoaded(loadGeometry, geometryRef)
    const label = conformerLabel(conformer)
    const atoms = geometry.status === "ready" ? geometry.data.atoms ?? [] : []
    return (
        <div className="shape-stage">
            <SectionHeading id="shape-view" subsection label={`${label} in 3D`}>{label} in 3D</SectionHeading>
            {geometryRef === null && <p className="empty-projection">No picture: no geometry is stored for this shape.</p>}
            {geometryRef !== null && (geometry.status === "loading" || geometry.status === "idle") && (
                <p className="note" role="status" aria-busy="true">Loading the geometry.</p>
            )}
            {(geometry.status === "failed" || geometry.status === "rate-limited" || (geometry.status === "ready" && atoms.length === 0)) && (
                <p className="empty-projection">No picture: the geometry could not be read.</p>
            )}
            {geometry.status === "ready" && atoms.length > 0 && (
                <>
                    <ViewerStage
                        key={geometryRef}
                        atoms={atoms}
                        formula={formula}
                        xyzText={geometry.data.xyz_text ?? null}
                    />
                    <p className="note">
                        Drawn from <Link to={`/geometries/${geometryRef}`}><code className="t-preserve-case">{geometryRef}</code></Link>, the first
                        final geometry deposited for this shape. Other observations of it may differ slightly.
                    </p>
                </>
            )}
        </div>
    )
}
