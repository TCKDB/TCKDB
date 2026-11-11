/**
 * Builds the reactant/product SIDES of a reaction equation from
 * `/full`'s `species.reactants[]`/`species.products[]` participants.
 *
 * A depositor can list the SAME species entry more than once on one side
 * (an equation written out atom by atom, e.g. `[NH2] + [NH2] <=> NN`
 * rather than a single row carrying `stoichiometry: 2`) -- this module
 * collapses repeated identical `species_entry_ref`s on one side into ONE
 * displayed participant with a summed coefficient, EXACT (the sum of each
 * grouped row's own `stoichiometry`, never a guess or a re-count of rows).
 * A single row already carrying `stoichiometry: 2` collapses to the same
 * shape trivially (one row, coefficient 2).
 *
 * `stoichiometry` is a §3A-additive field (not yet deployed as of this
 * PR): a participant row from a pre-deployment API has no `stoichiometry`
 * key at all, and the api layer (`api/reactionEntryApi.ts`) normalises
 * that absence to `1` before this module ever sees it -- one displayed
 * occurrence, the honest reading of "this API does not yet say how many".
 */

import { participantChips, type ParticipantFacets } from "./participantFacets"

export interface EquationParticipantInput extends ParticipantFacets {
    species_entry_ref: string
    species_entry_label?: string | null
    smiles: string
    formula?: string | null
    stoichiometry: number
    participant_index: number
}

export interface EquationParticipant {
    speciesEntryRef: string
    /** Chips from the structured facets ("Z isomer", "excited state"), never from the joined label. */
    chips: string[]
    smiles: string
    formula: string | null
    coefficient: number
}

export interface EquationSides {
    reactants: EquationParticipant[]
    products: EquationParticipant[]
}

function collapseSide(participants: EquationParticipantInput[]): EquationParticipant[] {
    const sorted = [...participants].sort((a, b) => a.participant_index - b.participant_index)
    const order: string[] = []
    const byRef = new Map<string, EquationParticipant>()
    for (const participant of sorted) {
        const existing = byRef.get(participant.species_entry_ref)
        if (existing) {
            existing.coefficient += participant.stoichiometry
            continue
        }
        byRef.set(participant.species_entry_ref, {
            speciesEntryRef: participant.species_entry_ref,
            chips: participantChips(participant),
            smiles: participant.smiles,
            formula: participant.formula ?? null,
            coefficient: participant.stoichiometry,
        })
        order.push(participant.species_entry_ref)
    }
    return order.map((ref) => byRef.get(ref)!)
}

export function buildEquationSides(
    reactants: EquationParticipantInput[],
    products: EquationParticipantInput[],
): EquationSides {
    return { reactants: collapseSide(reactants), products: collapseSide(products) }
}

/** The equation as plain text for a page title: "CH4 + OH → CH3 + H2O". */
export function equationText(
    reactants: EquationParticipantInput[],
    products: EquationParticipantInput[],
    reversible: boolean,
): string {
    const sides = buildEquationSides(reactants, products)
    const side = (participants: EquationParticipant[]) => participants
        .map((p) => `${p.coefficient > 1 ? `${p.coefficient} ` : ""}${p.formula ?? p.smiles}`)
        .join(" + ")
    return `${side(sides.reactants)} ${reversible ? "⇌" : "→"} ${side(sides.products)}`
}

const spoken = (participant: EquationParticipant) =>
    `${participant.coefficient > 1 ? `${participant.coefficient} ` : ""}${participant.formula ?? participant.smiles}`

/** The equation read aloud: formulas, "plus" between molecules and the arrow in words. */
export function schemeAriaLabel(reactants: EquationParticipant[], products: EquationParticipant[], reversible: boolean): string {
    const side = (participants: EquationParticipant[]) => participants.map(spoken).join(" plus ")
    return `${side(reactants)} ${reversible ? "reacts reversibly with" : "reacts to form"} ${side(products)}`
}

/**
 * True when formulas alone cannot tell the participants apart: two distinct entries share a formula, or both sides
 * read the same. Then the equation shows SMILES with the formula in brackets.
 */
export function equationNeedsStructure(reactants: EquationParticipantInput[], products: EquationParticipantInput[]): boolean {
    const sides = buildEquationSides(reactants, products)
    const entriesByFormula = new Map<string, Set<string>>()
    for (const participant of [...sides.reactants, ...sides.products]) {
        if (!participant.formula) continue
        const entries = entriesByFormula.get(participant.formula) ?? new Set<string>()
        entries.add(participant.speciesEntryRef)
        entriesByFormula.set(participant.formula, entries)
    }
    if ([...entriesByFormula.values()].some((entries) => entries.size > 1)) return true
    const read = (participants: EquationParticipant[]) => participants.map((p) => `${p.coefficient} ${p.formula ?? p.smiles}`).join("+")
    return read(sides.reactants) === read(sides.products)
}
