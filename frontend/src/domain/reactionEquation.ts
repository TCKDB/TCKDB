/**
 * Builds the reactant/product SIDES of a reaction equation from
 * `/full`'s `species.reactants[]`/`species.products[]` participants --
 * see `docs/plans/reaction-entry-page.md` §4 "Reaction equation".
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

export interface EquationParticipantInput {
    species_entry_ref: string
    species_entry_label?: string | null
    smiles: string
    formula?: string | null
    stoichiometry: number
    participant_index: number
}

export interface EquationParticipant {
    speciesEntryRef: string
    speciesEntryLabel: string | null
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
            speciesEntryLabel: participant.species_entry_label ?? null,
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
