import { useState } from "react"
import { Link } from "react-router-dom"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { words } from "../domain/provenanceFormat"
import { Formula } from "./Formula"

// `words` returns `null` for a missing/empty token (a case none of the
// tokens on this component's own wire type can actually hit -- `species_entry_kind`,
// `electronic_state_kind` and `review.status` are all non-nullable enum
// strings) but the fallback keeps this call site total rather than
// asserting a non-null enum shape it doesn't otherwise depend on.
function displayToken(value: string) {
    return words(value) ?? value
}

function availabilityText(entry: SpeciesEntryProjection) {
    return `${entry.availability.has_conformers ? "Conformers" : "No conformers"}`
        + `${entry.availability.has_thermo ? " · thermo" : ""}`
        + `${entry.availability.has_statmech ? " · statmech" : ""}`
        + `${entry.availability.has_transport ? " · transport" : ""}`
}

/**
 * Chemistry leads, references follow. The formula is the largest thing on
 * the page; the SMILES string sits directly beneath it as a "chemistry"
 * fact, not a database field. Stable public refs (`spc_…`, `spe_…`) move
 * to a quiet strip below the fact row — still visible, still copyable
 * (`CopyButton`), never competing with the science for the reader's first
 * look. See the design brief: "it shows the public ref but that's
 * terrible to show" was about equal billing, not about hiding it.
 */
export function EntryIdentity({ entry }: { entry: SpeciesEntryProjection }) {
    return <header className="entry-hero">
        <p className="eyebrow">Species entry · deposited scientific record</p>
        <div className="entry-formula-row">
            <h1>{entry.formula ? <Formula value={entry.formula} /> : entry.canonicalSmiles}</h1>
            <span className="state-chip">{displayToken(entry.electronic_state_kind)}</span>
        </div>
        <p className="entry-smiles"><code>{entry.canonicalSmiles}</code></p>
        <ul className="entry-facts" aria-label="Record facts">
            <FactItem label="Entry kind / state" value={`${displayToken(entry.species_entry_kind)} / ${displayToken(entry.electronic_state_kind)}`} />
            <FactItem label="Charge / multiplicity" value={`${chargeDisplay(entry.charge)} / ${spinDisplay(entry.multiplicity)}`} />
            <FactItem label="Review" value={displayToken(entry.review.status)} />
            <FactItem label="Archive availability" value={availabilityText(entry)} />
        </ul>
        <div className="ref-strip" aria-label="Stable references for this record">
            <RefItem label="Species" value={entry.speciesRef} to={`/species/${entry.speciesRef}`} />
            <RefItem label="Entry" value={entry.species_entry_ref} />
            <RefItem label="InChIKey" value={entry.inchiKey} />
        </div>
    </header>
}

function FactItem({ label, value }: { label: string; value: string }) {
    return <li><span>{label}</span><strong>{value}</strong></li>
}

function RefItem({ label, value, to }: { label: string; value: string; to?: string }) {
    return <div className="ref-item">
        <span className="ref-item-label">{label}</span>
        {to ? <Link to={to}>{value}</Link> : <span className="ref-item-value">{value}</span>}
        <CopyButton value={value} label={label} />
    </div>
}

function CopyButton({ value, label }: { value: string; label: string }) {
    const [copied, setCopied] = useState(false)
    return <button
        type="button"
        className="copy-button"
        data-copied={copied}
        aria-label={`Copy ${label} reference`}
        onClick={() => {
            if (!navigator.clipboard) return
            navigator.clipboard.writeText(value)
                .then(() => {
                    setCopied(true)
                    setTimeout(() => setCopied(false), 1500)
                })
                .catch(() => {
                    // Clipboard access can be denied or unavailable; the ref
                    // text stays selectable on the page either way.
                })
        }}
    >{copied ? "Copied" : "Copy"}</button>
}
