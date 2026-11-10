import { facetChips, type EntryFacetAxes } from "./recordFacets"

/**
 * The computed identity facets the API serves on every block that names a species entry (a reaction participant, a
 * network state participant, a record's species context). They are the same fields the species pages get, so the
 * same `facetChips` renders them. `electronic_state_label` and `term_symbol` are served only when they are in the
 * grammar the API enforces on write; `facetChips` checks that again and shows them as their own chips (`state T1`, `²Π`).
 */
export interface ParticipantFacets {
    species_entry_kind?: string | null
    electronic_state_kind?: string | null
    stereo_label?: string | null
    stereo_descriptor?: string | null
    stereo_resolution?: string | null
    isotope_key?: string | null
    electronic_state_label?: string | null
    term_symbol?: string | null
}

/**
 * Chips for a participant, built only from the structured facets. The two defaults every ordinary participant has
 * ("minimum", "ground state") are left out: they are noise beside a formula. An older payload that serves none of
 * the facets gets no chips, never a guess.
 */
export function participantChips(facets: ParticipantFacets | null | undefined): string[] {
    if (!facets || (facets.species_entry_kind == null && facets.electronic_state_kind == null)) return []
    const axes: EntryFacetAxes = {
        species_entry_kind: facets.species_entry_kind ?? "minimum",
        electronic_state_kind: facets.electronic_state_kind ?? "ground",
        stereo_label: facets.stereo_label,
        stereo_descriptor: facets.stereo_descriptor,
        stereo_resolution: facets.stereo_resolution,
        isotope_key: facets.isotope_key,
        electronic_state_label: facets.electronic_state_label,
        term_symbol: facets.term_symbol,
    }
    return facetChips(axes).filter((chip) => chip !== "minimum" && chip !== "ground state")
}

/**
 * A network state's one-string spelling (`N=N (Z isomer) + 2 [H][H]`), rebuilt from the participants' canonical SMILES,
 * stoichiometry and structured facets only. The server's own `state_label` embeds each participant's
 * `species_entry_label`, which joins in depositor-typed parts, so it is never used: no participants gives "".
 */
export function publicStateLabel(composition: {
    participants?: ReadonlyArray<ParticipantFacets & { canonical_smiles: string; stoichiometry: number }>
    participants_truncated?: boolean
    state_label: string
}): string {
    const participants = composition.participants ?? []
    if (participants.length === 0) return ""
    const parts = participants.map((participant) => {
        const chips = participantChips(participant)
        const face = chips.length > 0 ? `${participant.canonical_smiles} (${chips.join(", ")})` : participant.canonical_smiles
        return participant.stoichiometry !== 1 ? `${participant.stoichiometry} ${face}` : face
    })
    if (composition.participants_truncated) parts.push("...")
    return parts.join(" + ")
}
