import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import { SpeciesFace } from "./Formula"
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
 * Each participant links to its species entry through `SpeciesFace`
 * (`./Formula.tsx`): SMILES leads, in `code.data`, with the served
 * `formula` -- still typeset with subscripts -- following in parentheses
 * (`formula` is a §3A field an older/pre-deployment response can omit, in
 * which case the SMILES stands alone). This is the fix for the owner's
 * own reported defect: `rxn_fktlilofmrdaylunqva2hbltpq` rendered as
 * "CH3OS <=> CH3OS" because a formula-only face cannot tell reactant
 * `[CH2]SO` and product `OC[S]` apart -- they share a formula and nothing
 * else. Deliberately NOT `SpeciesEntryLink` here (unlike every other formula
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
 *
 * `linkParticipants` (default `true`, so every current caller --
 * `ReactionEntryPage`/`ReactionOverviewPage` -- is unaffected) opts a
 * caller OUT of the per-participant `/species-entries/:ref` links,
 * rendering the same formula/chip content as plain text instead. The one
 * caller that passes `false`: `ReactionBrowseRow`, which wraps the whole
 * equation in its OWN single `<Link>` to the reaction entry -- with
 * participant links left on, that outer link would nest `<a>` inside
 * `<a>` (browsers silently break the outer one past the first nested
 * link), which is the exact defect this prop exists to let a browse row
 * opt out of.
 */
export function ReactionEquation({ reactants, products, reversible, linkParticipants = true }: {
    reactants: EquationParticipantInput[]
    products: EquationParticipantInput[]
    reversible: boolean
    linkParticipants?: boolean
}) {
    const { reactants: reactantSide, products: productSide } = buildEquationSides(reactants, products)
    return (
        <>
            {renderSide(reactantSide, "reactant", linkParticipants)}
            {NBSP}
            <span aria-label={reversible ? "reacts reversibly with" : "reacts to form"}>
                {reversible ? "⇌" : "→"}
            </span>
            <wbr />
            {" "}
            {renderSide(productSide, "product", linkParticipants)}
        </>
    )
}

function renderSide(participants: EquationParticipant[], keyPrefix: string, linkParticipants: boolean): ReactNode[] {
    const nodes: ReactNode[] = []
    participants.forEach((participant, index) => {
        if (index > 0) {
            nodes.push(
                <span key={`${keyPrefix}-sep-${index}`}>
                    {NBSP}+<wbr />{" "}
                </span>,
            )
        }
        nodes.push(
            <EquationParticipantFace
                key={participant.speciesEntryRef}
                participant={participant}
                linked={linkParticipants}
            />,
        )
    })
    return nodes
}

function EquationParticipantFace({ participant, linked }: { participant: EquationParticipant; linked: boolean }) {
    const face = <SpeciesFace smiles={participant.smiles} formula={participant.formula} />
    const chip = participant.speciesEntryLabel && (
        <span className="reaction-equation-chip"> · {stereoChip(participant.speciesEntryLabel)}</span>
    )
    return (
        <>
            {participant.coefficient > 1 && `${participant.coefficient} `}
            {linked
                ? (
                    <Link to={`/species-entries/${participant.speciesEntryRef}`}>
                        {face}
                        {chip}
                    </Link>
                )
                : (
                    <>
                        {face}
                        {chip}
                    </>
                )}
        </>
    )
}
