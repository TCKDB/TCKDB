import type { ConformerProjection } from "../api/speciesEntryApi"
import { shapeRows } from "../domain/conformerGallery"
import type { SinglePointsState } from "../hooks/useSpeciesSinglePoints"
import { OrderNote } from "./OrderNote"
import { SectionHeading } from "./PageSections"
import { ShapeEnergies } from "./ShapeEnergies"

/**
 * The calculations of the selected shape and every energy computed on its geometry (single-point energies and the
 * final energy of each optimisation), grouped where they say the same thing. Nothing is picked or averaged.
 */
export function ShapeCalculations({ conformer, singlePoints, optimisations }: {
    conformer: ConformerProjection
    singlePoints: SinglePointsState
    optimisations: SinglePointsState
}) {
    const spRecords = singlePoints.status === "ready" ? singlePoints.records : []
    const optRecords = optimisations.status === "ready" ? optimisations.records : []
    const rows = shapeRows(conformer, spRecords, optRecords)
    const waiting = singlePoints.status === "loading" || optimisations.status === "loading"
    const capped = (singlePoints.status === "ready" && singlePoints.capped) || (optimisations.status === "ready" && optimisations.capped)
    return (
        <section className="shape-calculations" aria-labelledby="shape-calculations">
            <SectionHeading id="shape-calculations">Calculations of this shape</SectionHeading>
            {waiting && (
                <p className="note" role="status" aria-busy="true">The energies are still being read; an energy shown as missing may not have arrived yet.</p>
            )}
            {singlePoints.status === "failed" && <p className="note" role="alert">The single-point energies could not be read.</p>}
            {optimisations.status === "failed" && <p className="note" role="alert">The final energies of the optimisations could not be read.</p>}
            {capped && (
                <p className="note">This entry has more energies than this page reads. One shown as missing may not have been read.</p>
            )}
            {rows.length === 0 ? (
                <p className="empty-projection">No deposits of this shape are listed.</p>
            ) : (
                <>
                    {rows.length > 1 && <OrderNote />}
                    <ShapeEnergies rows={rows} singlePoints={singlePoints} optimisations={optimisations} />
                </>
            )}
        </section>
    )
}
