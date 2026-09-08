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
 * Link target is `/reactions/:reaction_ref` (the CHOOSER), not
 * `/reaction-entries/:reaction_entry_ref` (one deposit) -- owner-reported
 * inconsistency fix: "when we browse the archive for reactions and select
 * a reaction it should not go straight to the entry it should go to the
 * reaction and there the user selects the relevant entry - like how we do
 * with species". `SpeciesBrowseRow.tsx` already does exactly this
 * (title -> `/species/:species_ref`, the identity/chooser; each entry ->
 * its own `/species-entries/:ref` as a SEPARATE, secondary link) -- this
 * row now mirrors that same identity/entry relationship instead of
 * skipping straight to the one deposit. `ReactionOverviewPage.tsx`
 * (`/reactions/:reactionRef`) is that chooser: it lists every entry under
 * this reaction identity and lets the reader pick, the same job
 * `SpeciesOverviewPage`-adjacent chooser semantics already serve for
 * species. A `reaction_ref` handed a `rxe_...` value there redirects by
 * prefix check, so `record.reaction_ref` (never `record.reaction_entry_ref`)
 * is always the right target here.
 *
 * The one entry this row itself represents is still reachable -- as a
 * SECOND, explicit link in the footer (`browse-row-entry-link`,
 * `/reaction-entries/:reaction_entry_ref`), not folded into the title.
 * This is `SpeciesBrowseRow`'s own solution to reaching an individual
 * deposit without nesting anchors, carried over: a link to the entry that
 * is a SIBLING of the title link, never nested inside it. The footer
 * already had to solve exactly this "must stay clickable/selectable
 * despite the full-row stretched overlay" problem for the ref text (see
 * the CSS mechanic below) -- putting the new entry link there costs no
 * new elevation rule, since `.reaction-browse-row .browse-row-footer`
 * already paints above the overlay.
 *
 * Owner-reported defect fixed previously (kept as-is here): this row used
 * to render `ReactionEquation` with its participant links left ON (one
 * per participant, to `/species-entries/:ref`) and stack an invisible
 * row-wide `.browse-row-stretched-link` UNDER it for the reaction itself
 * -- almost every pixel of the equation was a species link, and the
 * reaction link only caught the gaps between glyphs. The premise that
 * pattern rested on ("the equation must keep its per-participant links
 * here, so the row link has to be a separate element to avoid nesting
 * `<a>` in `<a>`") was the bug, not the nesting concern itself: a browse
 * row does not need per-species links at all -- a reader who wants a
 * specific participant can already reach it from the reaction entry page
 * this row links to, where `ReactionEquation` still renders them (see
 * `ReactionEntryPage.tsx`/`ReactionOverviewPage.tsx`, both unchanged).
 * So this row still passes `linkParticipants={false}` (the opt-out
 * `ReactionEquation.tsx` added for exactly this caller), turning the
 * equation into plain text, and wraps that plain text in ONE real
 * `<Link>` to the reaction identity -- **still one click target for the
 * whole card**, per the owner's own follow-up instruction that this stay
 * true even after the target changes: nested anchors break, and
 * per-participant links made the row nearly unclickable, so that earlier
 * fix is preserved unchanged; only WHERE the title link now points has
 * moved. This is `TransitionStateBrowseRow`'s own `.browse-row-title
 * ::after` mechanic (shared class, shared selector shape, scoped by
 * `.reaction-browse-row` in `browse.css` instead of `.ts-browse-row`):
 * the `<Link>` wraps the visible title content, and an
 * absolutely-positioned empty `::after` pseudo-element stretches it
 * (`inset: 0`) to the row's full box against `.reaction-browse-row`'s own
 * `position: relative` -- a click anywhere in the card lands on that one
 * anchor. The footer (so the ref AND the new entry link stay reachable,
 * the same non-goal `TransitionStateBrowseRow`'s own doc comment names)
 * is given its own `position: relative` there so it paints above the
 * overlay per DOM order, the identical mechanic that component's own
 * comment documents in more depth. The pills row (`.browse-row-entries`)
 * is deliberately NOT elevated, matching that component's own choice --
 * neither review nor the two availability pills carry a value a reader
 * needs to select or click through, so leaving them inside the click
 * target does not cost anything.
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
    const target = `/reactions/${record.reaction_ref}`
    const entryTarget = `/reaction-entries/${record.reaction_entry_ref}`
    const reviewStatusText = token(record.review.status)
    const familyText = record.family ? token(record.family) : null
    const matchedReverse = record.matched_direction === "reverse"

    return (
        <li className="browse-row card reaction-browse-row">
            <div className="browse-row-headline">
                <p className="reaction-browse-row-title">
                    {/* Deliberately NO `aria-label` here: the equation is the
                        Link's own visible content (`species_entry_label` chip
                        and the arrow's "reacts reversibly with"/"reacts to
                        form" aria-label included), so the browser's own
                        accname computation already reads as an English
                        sentence -- e.g. "H2N + H2N reacts reversibly with
                        H4N2". A served `record.equation` string (raw
                        SMILES-joined form, e.g. "[NH2] + [NH2] <=> NN") is
                        NOT a substitute: measured live via Chrome's
                        accessibility tree, that string shares zero words
                        with the rendered formula text, a WCAG 2.5.3 "Label
                        in Name" failure -- a voice-control user saying
                        "click H2N" cannot activate the row, and a screen
                        reader spells out a SMILES string letter by letter
                        instead of reading the formula it corresponds to.
                        An earlier revision of this component set
                        `aria-label={record.equation ?? undefined}`
                        specifically to get an "exact, testable string" the
                        way `TransitionStateBrowseRow` does -- but that
                        component's own aria-label is built from the SAME
                        rendered-equation string its Link displays (plus the
                        TS label), never a differently-formatted one; there
                        is no equivalent here to reach for, so the honest
                        fix is no override at all. */}
                    <Link className="browse-row-title" to={target}>
                        <ReactionEquation
                            linkParticipants={false}
                            products={toEquationParticipants(record.products)}
                            reactants={toEquationParticipants(record.reactants)}
                            reversible={record.reversible}
                        />
                    </Link>
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
                <code className="browse-ref data">{record.reaction_ref}</code>
                {/* The one deposit THIS row itself represents -- a second,
                    SIBLING link (never nested inside the title's own
                    `<Link>`), reachable even though the title now points at
                    the reaction identity/chooser instead. Sits inside
                    `.browse-row-footer`, which is already elevated above
                    the full-row stretched overlay (see this component's own
                    doc comment and the CSS rule below) for exactly this
                    reason -- no new elevation rule needed on top of the one
                    the ref text already required. */}
                <Link className="browse-row-entry-link" to={entryTarget}>View this deposit</Link>
            </p>
        </li>
    )
}
