import { Link } from "react-router-dom"
import type { SpeciesBrowseRecord } from "../api/browseApi"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { Formula } from "./Formula"

function token(value: string) {
    return value.replaceAll("_", " ")
}

/**
 * A species row leads with FORMULA AND STRUCTURE -- the brief's own
 * distinction from the transition-state row, which has no formula and
 * leads with the reaction it connects instead. Formula falls back to the
 * SMILES headline (never to the public ref, matching `IdentifierSearch`'s
 * `MatchHeadline` rule) when the archive computed no formula for this
 * species (#251).
 */
export function SpeciesBrowseRow({ record }: { record: SpeciesBrowseRecord }) {
    return (
        <li className="browse-row species-browse-row">
            <div className="browse-row-headline">
                <Link className="browse-row-title" to={`/species/${record.species_ref}`}>
                    {record.formula ? <Formula value={record.formula} /> : record.canonical_smiles}
                </Link>
                <span className="browse-row-meta">
                    charge {chargeDisplay(record.charge)} · spin {spinDisplay(record.multiplicity)}
                </span>
            </div>
            {record.formula && <p className="browse-row-smiles">{record.canonical_smiles}</p>}
            <ul className="browse-row-entries">
                {record.entries.map((entry) => (
                    // Two SEPARATE pills, not one shared box -- classification
                    // (kind · state) and review status are different axes (the
                    // former is what the entry IS, the latter is curation
                    // status), and putting both inside one bordered chip made
                    // them read as a single fact ("NOT REVIEWED is part of the
                    // same pill as minimum · ground which should not be so").
                    <li className="browse-entry-chip" key={entry.species_entry_ref}>
                        <span className="value-pill browse-entry-kind-pill">
                            <Link to={`/species-entries/${entry.species_entry_ref}`}>
                                {token(entry.species_entry_kind)} · {token(entry.electronic_state_kind)}
                            </Link>
                        </span>
                        <span className="value-pill value-pill--muted browse-entry-review">{token(entry.review.status)}</span>
                    </li>
                ))}
            </ul>
            <code className="browse-row-ref">{record.species_ref}</code>
        </li>
    )
}
