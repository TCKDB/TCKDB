import type { ConformerProjection } from "../api/speciesEntryApi"
import { calculationTypeCounts, conformerLabel } from "../domain/conformerEvidence"
import { RefsDisclosure } from "./RefsDisclosure"

// The heading is imperative ("Choose a conformer") only when there is an
// actual choice to make. At zero or one conformer there is nothing to
// choose, so the heading says what's there instead of issuing an
// instruction that doesn't apply -- the note below it already adapts the
// same way; the heading previously didn't.
function pickerHeading(count: number): string {
    if (count === 0) return "Conformers"
    if (count === 1) return "Conformer"
    return "Choose a conformer"
}

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
            <h2 id="conformer-picker-title">{pickerHeading(conformers.length)}</h2>
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

// A `<details>` cannot nest inside a `<button>` (both are interactive
// content), so the card is a non-interactive wrapper around two separate
// controls: the button that selects this conformer (label, observation/
// calculation counts, coverage -- everything that distinguishes one card
// from another), and a References disclosure for the ref alone. The label
// stays the primary distinguisher and stays visible outside the disclosure.
function ConformerCard({ conformer, isSelected, onSelect }: {
    conformer: ConformerProjection
    isSelected: boolean
    onSelect: (conformerGroupRef: string) => void
}) {
    const ref = conformer.conformer_group.conformer_group_ref
    const total = conformer.observations_summary.total
    const coverage = conformer.evidence_summary.evidence_coverage
    const typeCounts = calculationTypeCounts(conformer)
    return (
        <div className="conformer-card" data-selected={isSelected}>
            <button
                type="button"
                className="conformer-card-select"
                aria-pressed={isSelected}
                onClick={() => onSelect(ref)}
            >
                <span className="conformer-card-label">{conformerLabel(conformer)}</span>
                <span className="conformer-card-meta">
                    {total} observation{total === 1 ? "" : "s"} · {conformer.evidence_summary.calculation_count} calculation rows
                    {typeCounts.length > 0 && ` (${typeCounts.map(({ type, count }) => `${count} ${type}`).join(" · ")})`}
                </span>
                <span className="conformer-card-coverage">
                    opt {coverage.opt}/{total} obs · freq {coverage.freq}/{total} obs · sp {coverage.sp}/{total} obs
                </span>
            </button>
            <RefsDisclosure refs={[{ label: "Conformer group", value: ref, to: `/conformer-groups/${ref}` }]} />
        </div>
    )
}
