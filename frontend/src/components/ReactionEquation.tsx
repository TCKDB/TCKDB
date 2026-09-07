import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import { SpeciesEntryLink } from "./SpeciesEntryLink"
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
 * Each participant links to its species entry (`SpeciesEntryLink` when a
 * `formula` was served; a SMILES-in-data-face fallback, in the SAME
 * `Link`/label shape, when it was not -- `formula` is a §3A-additive field
 * and an older API/pre-deployment response omits it entirely). A
 * participant's coefficient (from `domain/reactionEquation.ts`'s exact
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
    const body = participant.formula
        ? (
            <SpeciesEntryLink
                speciesEntryRef={participant.speciesEntryRef}
                formula={participant.formula}
                speciesEntryLabel={participant.speciesEntryLabel}
            />
        )
        : (
            <Link to={`/species-entries/${participant.speciesEntryRef}`}>
                <code className="data">{participant.smiles}</code>
                {participant.speciesEntryLabel && <> · {stereoChip(participant.speciesEntryLabel)}</>}
            </Link>
        )
    return (
        <>
            {participant.coefficient > 1 && `${participant.coefficient} `}
            {body}
        </>
    )
}
