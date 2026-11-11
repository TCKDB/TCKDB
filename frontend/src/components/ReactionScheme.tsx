import { Fragment } from "react"
import { Link } from "react-router-dom"
import "../reaction-scheme.css"
import { buildEquationSides, schemeAriaLabel, type EquationParticipant, type EquationParticipantInput } from "../domain/reactionEquation"
import { Formula } from "./Formula"
import { ParticipantChips } from "./ParticipantChips"

function Participant({ participant }: { participant: EquationParticipant }) {
    return (
        <Link className="reaction-scheme-molecule" to={`/species-entries/${participant.speciesEntryRef}`}>
            <span className="reaction-scheme-formula">
                {participant.coefficient > 1 && <span className="t-preserve-case">{participant.coefficient} × </span>}
                {participant.formula ? <Formula value={participant.formula} /> : <code className="t-preserve-case">{participant.smiles}</code>}
                <ParticipantChips chips={participant.chips} />
            </span>
            {participant.formula && <code className="reaction-scheme-smiles t-preserve-case">{participant.smiles}</code>}
        </Link>
    )
}

/** The reaction as a row: reactants, the arrow, products. Each molecule links to its entry. */
export function ReactionScheme({ reactants, products, reversible }: {
    reactants: EquationParticipantInput[]
    products: EquationParticipantInput[]
    reversible: boolean
}) {
    const sides = buildEquationSides(reactants, products)
    const side = (participants: EquationParticipant[]) => participants.map((participant, index) => (
        <Fragment key={participant.speciesEntryRef}>
            {index > 0 && <span className="reaction-scheme-plus" aria-hidden="true">+</span>}
            <Participant participant={participant} />
        </Fragment>
    ))
    return (
        <div className="reaction-scheme" role="group" aria-label={schemeAriaLabel(sides.reactants, sides.products, reversible)}>
            <div className="reaction-scheme-side">{side(sides.reactants)}</div>
            <span className="reaction-scheme-arrow" aria-hidden="true">{reversible ? "⇌" : "→"}</span>
            <div className="reaction-scheme-side">{side(sides.products)}</div>
        </div>
    )
}
