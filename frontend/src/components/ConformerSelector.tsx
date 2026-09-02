import type { ConformerProjection } from "../api/speciesEntryApi"
import { calculationTypeCounts, conformerLabel, sortConformersForDisplay } from "../domain/conformerEvidence"
import { buildBasinRotors, formatDeg, formatRangeDeg } from "../domain/conformerFingerprint"
import { SectionHeading } from "./PageSections"
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
            <SectionHeading id="conformer-picker-title">{pickerHeading(conformers.length)}</SectionHeading>
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
                        {/* Display order only -- `conformers/search`'s own ranking
                            (review rank, then recency) is untouched everywhere else
                            this list is passed (default selection, attribution
                            lookups); this reorders only what's rendered here, so
                            "Conformer Group 1" reads first, then 2, then 3, not
                            whatever order the archive happened to rank them in. */}
                        {sortConformersForDisplay(conformers).map((conformer) => (
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
    const fingerprint = conformer.conformer_group.fingerprint
    const rotors = fingerprint ? buildBasinRotors(fingerprint) : null
    // One line, no wrap, per card (`species-entry.css`'s `.conformer-card-
    // meta`/`-coverage`) -- three fixed columns per row do not leave room
    // for the longest real value to fit unwrapped (measured: a 69-character
    // meta line exists in the live archive), so that CSS clips with an
    // ellipsis rather than wrapping or silently truncating. Built as a
    // plain string, once, here -- rather than as JSX with inline `{...}`
    // expressions the way this used to render -- so the exact same text
    // backs BOTH the visible (possibly clipped) line and its `title`
    // tooltip, which is what keeps the full value reachable on hover/
    // focus. The two can never drift apart because there is only one
    // string, not a rendered version and a separately-composed summary.
    const metaText = `${total} observation${total === 1 ? "" : "s"} · `
        + `${conformer.evidence_summary.calculation_count} calculation row${conformer.evidence_summary.calculation_count === 1 ? "" : "s"}`
        + (typeCounts.length > 0 ? ` (${typeCounts.map(({ type, count }) => `${count} ${type}`).join(" · ")})` : "")
    const coverageText = `opt ${coverage.opt}/${total} obs · freq ${coverage.freq}/${total} obs · sp ${coverage.sp}/${total} obs`
    return (
        <div className="conformer-card" data-selected={isSelected}>
            <button
                type="button"
                className="conformer-card-select"
                aria-pressed={isSelected}
                onClick={() => onSelect(ref)}
            >
                <span className="conformer-card-label">{conformerLabel(conformer)}</span>
                <span className="conformer-card-meta" title={metaText}>{metaText}</span>
                <span className="conformer-card-coverage" title={coverageText}>{coverageText}</span>
            </button>
            {/* `rotors` is `null` when the archive returned no fingerprint at
                all for this group (not requested, or the row itself is
                malformed) -- rendered as nothing extra, same as before. Once
                a fingerprint IS present, an empty `rotors` array (37 of 66
                groups measured -- the MAJORITY, not an edge case) is a
                positive fact -- a rigid molecule with no rotatable bonds --
                and gets its own sentence rather than silently rendering
                nothing, which would read as missing data instead of "there
                is nothing here to show". */}
            {rotors && (
                rotors.length > 0 ? (
                    <dl
                        className="conformer-basin-identity"
                        aria-label={`Numeric basin identity for ${conformerLabel(conformer)}`}
                    >
                        {rotors.map((rotor) => (
                            <div className="conformer-basin-rotor" key={rotor.rotorKey} data-rotor-key={rotor.rotorKey}>
                                <dt>{rotor.bondLabel}</dt>
                                <dd>
                                    {/* The basin (what DEFINES this group -- a degree RANGE,
                                        never the internal bin index a reader has no use for)
                                        and the representative (one member's own measured
                                        angle) are separately labelled -- never collapsed into
                                        a single number. See `domain/conformerFingerprint.ts`. */}
                                    <span className="basin-range">
                                        basin {formatRangeDeg(rotor.binRangeDeg)}
                                        {rotor.isFolded ? " (folded coordinates)" : ""}
                                    </span>
                                    <span className="basin-representative">
                                        representative {formatDeg(rotor.representativeRawDeg)}
                                        {rotor.representativeFoldedDeg !== null
                                            ? ` (folds to ${formatDeg(rotor.representativeFoldedDeg)})`
                                            : ""}
                                    </span>
                                </dd>
                            </div>
                        ))}
                    </dl>
                ) : (
                    <p className="conformer-basin-rigid">
                        No rotatable bonds recorded for this basin — a single rigid conformer, not one of
                        several possible torsional arrangements.
                    </p>
                )
            )}
            <RefsDisclosure refs={[{ label: "Conformer group", value: ref, to: `/conformer-groups/${ref}` }]} />
        </div>
    )
}
