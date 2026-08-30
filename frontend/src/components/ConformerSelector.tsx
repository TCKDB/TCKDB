import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel } from "../domain/conformerEvidence"

/**
 * "Choose a conformer, then see its geometry, single-point energy,
 * statistical mechanics and thermochemistry" -- the owner's own words for
 * this page. A plain list of selectable basin cards, not a chapter of
 * links: choosing one is the entry point to every tab below, not a
 * destination in itself. Scales from the degenerate case (one basin, one
 * card, already selected) to many without changing shape -- see the design
 * report for how this renders at 1 / 4 / many.
 */
export function ConformerSelector({ conformers, selectedRef, onSelect }: {
    conformers: ConformerProjection[]
    selectedRef: string | null
    onSelect: (conformerGroupRef: string) => void
}) {
    return (
        <section className="conformer-picker" aria-labelledby="conformer-picker-title">
            <p className="eyebrow">Conformers</p>
            <h2 id="conformer-picker-title">Choose a conformer</h2>
            {conformers.length === 0 ? (
                <p className="empty-projection">No conformer basins are projected for this entry.</p>
            ) : (
                <>
                    <p className="conformer-picker-note">
                        {conformers.length === 1
                            ? "This entry has one deposited conformer basin. Its evidence is shown below."
                            : `This entry has ${conformers.length} deposited conformer basins. Choose one to see `
                                + "its geometry, single-point energy, statistical mechanics and thermochemistry."}
                    </p>
                    <div className="conformer-list" role="group" aria-labelledby="conformer-picker-title">
                        {conformers.map((conformer) => (
                            <ConformerCard
                                key={conformer.conformer_group.conformer_group_ref}
                                conformer={conformer}
                                isSelected={conformer.conformer_group.conformer_group_ref === selectedRef}
                                onSelect={onSelect}
                            />
                        ))}
                    </div>
                </>
            )}
        </section>
    )
}

function ConformerCard({ conformer, isSelected, onSelect }: {
    conformer: ConformerProjection
    isSelected: boolean
    onSelect: (conformerGroupRef: string) => void
}) {
    const ref = conformer.conformer_group.conformer_group_ref
    const total = conformer.observations_summary.total
    const coverage = conformer.evidence_summary.evidence_coverage
    return (
        <button
            type="button"
            className="conformer-card"
            aria-pressed={isSelected}
            data-selected={isSelected}
            onClick={() => onSelect(ref)}
        >
            <span className="conformer-card-label">{conformerLabel(conformer)}</span>
            <code className="conformer-card-ref">{ref}</code>
            <span className="conformer-card-meta">
                {total} observation{total === 1 ? "" : "s"} · {conformer.evidence_summary.calculation_count} calculation rows
            </span>
            <span className="conformer-card-coverage">
                opt {coverage.opt}/{total} · freq {coverage.freq}/{total} · sp {coverage.sp}/{total}
            </span>
        </button>
    )
}
