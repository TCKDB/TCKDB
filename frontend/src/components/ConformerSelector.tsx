import type { ConformerProjection } from "../api/speciesEntryApi"
import { calculationTypeCounts, conformerLabel, sortConformersForDisplay } from "../domain/conformerEvidence"
import { buildBasinRotors, formatDeg, formatRangeDeg, parseRotorKey } from "../domain/conformerFingerprint"
import { SectionHeading } from "./PageSections"
import { RefsDisclosure } from "./RefsDisclosure"

// A dihedral is defined by four atoms, so a rotor row reading "ATOMS 8–10"
// -- an en dash, right beside a basin range that ALSO reads "345–360°" --
// is misread as "atoms 8 through 10", a run of three atoms, not the two
// atom indices that anchor the rotatable bond itself. `rotor.bondLabel`
// (built by `domain/conformerFingerprint.ts`'s `rotorBondLabel`) already
// says "atoms 8–10" for exactly that reason -- this local override only
// changes the WORD in front of the numbers, from "atoms" to "bond", since
// what the pair of indices actually identifies is the bond the torsion
// measures, not a three-atom span. Kept local to this component (rather
// than editing the shared domain helper) because this fix is scoped to
// this card's rendered text, not to every consumer of that helper.
//
// The basin range's own top edge is left as "345–360°" rather than folded
// to "345–0°": a 0–360° torsion range is conventionally read as wrapping,
// but flipping the displayed high edge to a number SMALLER than the low
// edge reads as a reversed or broken range at a glance, which is a worse
// misreading than the one being fixed here. The "bond" relabel above
// already removes the reading that motivated this -- "bond 8–10" next to
// "basin 345–360°" no longer looks like one four-number atom span.
function bondRowLabel(rotorKey: string): string {
    const pair = parseRotorKey(rotorKey)
    return pair ? `bond ${pair.atomA}–${pair.atomB}` : rotorKey
}

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
                <p className="empty-projection">No conformers are recorded for this entry.</p>
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
    // meta`/`-coverage`). Abbreviated deliberately: the long form
    // ("2 observations · 8 calculation rows (...)") measured 69 characters,
    // which needs a 34rem card, which meant only two cards fit a 1920px
    // screen -- so three-per-row and no-wrap could not both hold. Shortening
    // the text was chosen over narrowing the cards (which reintroduces
    // wrapping) or clipping (which the owner rejected outright). "obs" is
    // already the unit the coverage line below uses, so the abbreviation is
    // not introducing new vocabulary. Built as one plain string so the same
    // text backs the visible line and its `title`; the two cannot drift.
    const metaText = `${total} obs · ${conformer.evidence_summary.calculation_count} calc`
        + `${conformer.evidence_summary.calculation_count === 1 ? "" : "s"}`
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
                                <dt>{bondRowLabel(rotor.rotorKey)}</dt>
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
            {/* `inset`: this disclosure sits inside the already-boxed
                `.conformer-card` -- the shared `.disclosure--inset`
                modifier (`design-system.css`) gives it a border-top-only
                separator instead of a second full box, the fix for the
                owner-reported double line before "References". Used to
                be a `.conformer-card .refs-disclosure` override in
                `species-entry.css`; retired in favour of this prop. */}
            <RefsDisclosure inset refs={[{ label: "Conformer group", value: ref, to: `/conformer-groups/${ref}` }]} />
        </div>
    )
}
