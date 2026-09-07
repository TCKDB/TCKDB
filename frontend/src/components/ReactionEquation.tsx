import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import { Formula } from "./Formula"
import { buildEquationSides, type EquationParticipant, type EquationParticipantInput } from "../domain/reactionEquation"
import { stereoChip } from "../domain/recordFacets"

const NBSP = " "

/**
 * The reaction equation as this app's shared h1 -- reused verbatim by
 * `ReactionEntryPage` (title = the equation) and `ReactionOverviewPage`
 * (chooser h1), and promoted here from `TransitionStateEntryPage.tsx`'s
 * page-local `renderEquationWithBreaks` so a TS entry's own h1 (built from
 * the SAME `reaction.equation` string on that page, item: "both records of
 * one reaction must read alike") can migrate onto this component too where
 * that migration is contained -- see that page's own call site comment.
 *
 * Each participant links to its species entry: `Formula` (subscripted)
 * when a `formula` was served, a SMILES-in-data-face fallback when it was
 * not (`formula` is a §3A field an older/pre-deployment response can omit).
 * Deliberately NOT `SpeciesEntryLink` here (unlike every other formula
 * link in this app) -- that component renders its optional stereo-label
 * suffix (`species_entry_label`, e.g. "Z") as bare inherited-size text
 * with no wrapping element to scope. MEASURED (post-review): composed
 * into this h1 at `--type-display-2` (36px serif), "· Z isomer" inherited
 * the full 36px and read as the single largest, most prominent text on
 * the chooser page -- a plain-English aside outweighing the chemistry
 * itself. This component builds its own link markup instead so the chip
 * is a real element (`.reaction-equation-chip`) `reaction-entry.css` can
 * cap to body-text size independent of the surrounding h1's own step.
 * `code.data`'s own fixed `font: var(--type-data-font)` (13px mono) has
 * the opposite problem -- too SMALL inside a 36px h1 -- fixed the same
 * way, `font-size: inherit`.
 *
 * A participant's coefficient (from `domain/reactionEquation.ts`'s exact
 * collapse of repeated identical refs) renders as a plain leading number
 * only when greater than 1 -- "2 NH2", never "1 NH2".
 *
 * Every `+` and the arrow itself break with `&nbsp;<symbol><wbr> ` -- an
 * `&nbsp;` before the symbol keeps it glued to the PRECEDING participant
 * (a line never starts with a bare `+`/`⇌`), and the `<wbr>` right after it
 * is the one legal break point, so a long formula either side wraps at the
 * operator rather than at an arbitrary character. Mirrors the mock's own
 * `H<sub>2</sub>O&nbsp;+<wbr> CH<sub>3</sub>...` markup exactly.
 *
 * The arrow itself carries an `aria-label` (⇌ "reacts reversibly with" /
 * → "reacts to form") -- a screen reader has no other way to distinguish
 * the two Unicode arrow glyphs from each other by ear.
 */
export function ReactionEquation({ reactants, products, reversible }: {
    reactants: EquationParticipantInput[]
    products: EquationParticipantInput[]
    reversible: boolean
}) {
    const { reactants: reactantSide, products: productSide } = buildEquationSides(reactants, products)
    return (
        <>
            {renderSide(reactantSide, "reactant")}
            {NBSP}
            <span aria-label={reversible ? "reacts reversibly with" : "reacts to form"}>
                {reversible ? "⇌" : "→"}
            </span>
            <wbr />
            {" "}
            {renderSide(productSide, "product")}
        </>
    )
}

function renderSide(participants: EquationParticipant[], keyPrefix: string): ReactNode[] {
    const nodes: ReactNode[] = []
    participants.forEach((participant, index) => {
        if (index > 0) {
            nodes.push(
                <span key={`${keyPrefix}-sep-${index}`}>
                    {NBSP}+<wbr />{" "}
                </span>,
            )
        }
        nodes.push(<EquationParticipantLink key={participant.speciesEntryRef} participant={participant} />)
    })
    return nodes
}

function EquationParticipantLink({ participant }: { participant: EquationParticipant }) {
    const face = participant.formula
        ? <Formula value={participant.formula} />
        : <code className="data">{participant.smiles}</code>
    return (
        <>
            {participant.coefficient > 1 && `${participant.coefficient} `}
            <Link to={`/species-entries/${participant.speciesEntryRef}`}>
                {face}
                {participant.speciesEntryLabel && (
                    <span className="reaction-equation-chip"> · {stereoChip(participant.speciesEntryLabel)}</span>
                )}
            </Link>
        </>
    )
}
