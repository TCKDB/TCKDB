import { Link } from "react-router-dom"
import { ParticipantChips } from "./ParticipantChips"
import { SpeciesFace } from "./Formula"
import { participantChips, type ParticipantFacets } from "../domain/participantFacets"

/**
 * The "Species entry" link every record page that owns a species points at
 * `/species-entries/:ref`. The link text is `SpeciesFace` (SMILES, then the
 * formula in parentheses), or the entry's own ref when neither is served.
 *
 * It never prints anything from `species_entry_label`. That string is the
 * server's joined discriminator (stereo, state kind, state label, term symbol,
 * isotope key), and the state label and term symbol in it are depositor-typed;
 * a joined string cannot be taken apart on the client without guessing which
 * part is which. Pages that have the structured
 * fields (`stereo_label`, `electronic_state_kind`, `isotope_key`) show them
 * through `facetChips`.
 */
export function SpeciesEntryLink({ speciesEntryRef, smiles, formula, facets }: {
    speciesEntryRef: string
    smiles?: string | null
    formula?: string | null
    /** The computed identity facets the API serves beside the species context (never the joined label). */
    facets?: ParticipantFacets | null
}) {
    const chips = participantChips(facets)
    const base = (smiles || formula)
        ? <SpeciesFace smiles={smiles} formula={formula} />
        : <span>Species entry</span>
    return (
        <Link to={`/species-entries/${speciesEntryRef}`}>
            {base}
            <ParticipantChips chips={chips} />
        </Link>
    )
}
