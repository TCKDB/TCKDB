import { Link } from "react-router-dom"
import type { ReactionBrowseRecord } from "../api/browseApi"
import type { EquationParticipantInput } from "../domain/reactionEquation"
import { ReactionEquation } from "./ReactionEquation"

function token(value: string) {
    return value.replaceAll("_", " ")
}

function toEquationParticipants(participants: ReactionBrowseRecord["reactants"]): EquationParticipantInput[] {
    return participants.map((participant) => ({
        species_entry_ref: participant.species_entry_ref,
        species_entry_label: participant.species_entry_label,
        smiles: participant.smiles,
        formula: participant.formula,
        stoichiometry: participant.stoichiometry,
        participant_index: participant.participant_index,
    }))
}

/**
 * The "reaction" browse kind (PR 4b): leads with the equation (via the
 * shared `ReactionEquation` component PR 2 shipped -- imported, not
 * forked, per the plan's own instruction), then family, review, and the
 * two availability flags.
 *
 * Link target is `/reaction-entries/:ref`, per the plan's route key
 * (§2: `rxe_…` is the record page; `rxn_…` is the chooser). This row
 * links the ENTRY, not the identity, mirroring `TransitionStateBrowseRow`
 * linking `transition_state_entry_ref` rather than
 * `transition_state_ref`.
 *
 * Stretched-link mechanics differ from `TransitionStateBrowseRow`'s own
 * `.browse-row-title::after` pattern on purpose: that pattern puts the
 * `<Link>` around the row's OWN visible title text and stretches an empty
 * pseudo-element off of it. Here the "title" is `ReactionEquation`, which
 * already renders real `<Link>`s of its own (one per participant, to
 * `/species-entries/:ref`) -- nesting the whole equation inside a SECOND
 * anchor pointed at `/reaction-entries/:ref` would nest `<a>` inside
 * `<a>`, which browsers silently break (the outer link stops being
 * clickable past the first nested one). So the row-level link here is its
 * OWN separate, empty element (`.browse-row-stretched-link`, no visible
 * text, `aria-label` only) placed FIRST in the DOM and stretched via
 * `position: absolute; inset: 0` in `browse.css` -- the headline (so each
 * participant link stays individually clickable, matching
 * `EquationParticipantLink`'s own href) and the footer (so the ref stays
 * selectable, the same non-goal `TransitionStateBrowseRow`'s own doc
 * comment names) are given their own `position: relative` there so they
 * paint above the overlay per DOM order, the identical mechanic that
 * component's own comment documents in more depth. The pills row
 * (`.browse-row-entries`) is deliberately NOT elevated, matching that
 * component's own choice -- neither review nor the two availability pills
 * carry a value a reader needs to select, so leaving them inside the
 * click target does not cost anything.
 *
 * `matched_direction` (review follow-up, round 2): a reactant/product
 * SMILES search matches EITHER side of a reversible reaction, so the
 * equation as served can list the searched species on the side OPPOSITE
 * the one the reader searched -- e.g. a `productSmiles` search for water
 * can return an entry whose served equation reads "H2O + CH3 <=> CH4 + HO",
 * water on the reactant side, because the archive matched the reverse
 * direction. Rendered as a plain note (never a pill -- this is not a
 * categorical fact about the record the way review/kinetics/TS are, it is
 * a fact about how THIS SEARCH matched) only when the served value is
 * `"reverse"`; `"forward"` (the default, and what every unfiltered row
 * carries) and an absent/null field (an older API) both render nothing,
 * per the same absent-vs-asserted contract `familyText` follows above --
 * "forward" is not itself news, so it says nothing, matching the site's
 * "never assert from absence" rule read the other way: an ordinary match
 * gets no caveat.
 */
export function ReactionBrowseRow({ record }: { record: ReactionBrowseRecord }) {
    const target = `/reaction-entries/${record.reaction_entry_ref}`
    const reviewStatusText = token(record.review.status)
    const familyText = record.family ? token(record.family) : null
    const matchedReverse = record.matched_direction === "reverse"

    return (
        <li className="browse-row card reaction-browse-row">
            <Link
                aria-label={`View reaction entry ${record.reaction_entry_ref}`}
                className="browse-row-stretched-link"
                to={target}
            />
            <div className="browse-row-headline">
                <p className="reaction-browse-row-title">
                    <ReactionEquation
                        products={toEquationParticipants(record.products)}
                        reactants={toEquationParticipants(record.reactants)}
                        reversible={record.reversible}
                    />
                </p>
                <span className="browse-row-meta">
                    {familyText ?? <span className="absent">family not recorded</span>}
                </span>
            </div>
            <ul className="browse-row-entries">
                <li className="browse-entry-chip">
                    <span className="value-pill value-pill--muted browse-entry-review">{reviewStatusText}</span>
                </li>
                <li className="browse-entry-chip">
                    {/* Two per-record availability facts, one pill each -- never
                        "none exist", since these describe THIS entry's own
                        deposited evidence, not the archive as a whole. A muted
                        pill states the absence plainly rather than omitting the
                        fact ("no kinetics deposited", not silence). */}
                    <span className={record.availability.has_kinetics ? "value-pill" : "value-pill value-pill--muted"}>
                        {record.availability.has_kinetics ? "has kinetics" : "no kinetics deposited"}
                    </span>
                    <span className={record.availability.has_transition_state ? "value-pill" : "value-pill value-pill--muted"}>
                        {record.availability.has_transition_state ? "has transition state" : "no transition state deposited"}
                    </span>
                </li>
            </ul>
            <p className="browse-row-footer">
                {matchedReverse && (
                    <span className="browse-row-evidence">Matched on the reverse direction</span>
                )}
                <code className="browse-ref data">{record.reaction_entry_ref}</code>
            </p>
        </li>
    )
}
