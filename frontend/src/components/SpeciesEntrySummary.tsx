import { useState } from "react"
import { Link } from "react-router-dom"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { words } from "../domain/provenanceFormat"
import { sectionLabels } from "../domain/speciesEntrySections"
import type { EntrySection } from "../domain/speciesEntrySections"
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

/**
 * Three named chapter groups instead of one flat row of tabs: "Record"
 * (this identity, always the same regardless of chapter), "Evidence
 * chain" (how the result was produced — conformers, calculation stages),
 * and "Computed products" (what the entry asserts — thermo, statmech,
 * transport). The grouping is the record's real shape, not a cosmetic
 * split; see the design rationale for why this replaced a flat tab bar.
 */
const NAV_GROUPS: Array<{ caption: string; items: Array<{ path: string; label: string }> }> = [
    { caption: "Record", items: [{ path: "", label: "Overview" }] },
    {
        caption: "Evidence chain",
        items: [
            { path: "conformers", label: sectionLabels.conformers },
            { path: "calculations", label: sectionLabels.calculations },
        ],
    },
    {
        caption: "Computed products",
        items: [
            { path: "thermo", label: sectionLabels.thermo },
            { path: "statmech", label: sectionLabels.statmech },
            { path: "transport", label: sectionLabels.transport },
        ],
    },
]

export function EntryNavigation({ entryRef, activeSection }: { entryRef: string; activeSection: EntrySection }) {
    return <nav className="entry-chapters" aria-label="Entry chapters">
        {NAV_GROUPS.map((group) => <div className="chapter-group" key={group.caption}>
            <p className="chapter-group-label">{group.caption}</p>
            <div className="chapter-group-links">
                {group.items.map(({ path, label }) => {
                    const isActive = path === "" ? activeSection === "overview" : activeSection === path
                    return <Link
                        key={path || "overview"}
                        aria-current={isActive ? "page" : undefined}
                        to={path ? `/species-entries/${entryRef}/${path}` : `/species-entries/${entryRef}`}
                    >
                        {label}
                    </Link>
                })}
            </div>
        </div>)}
    </nav>
}

/**
 * The overview chapter's boolean manifest card for thermo/statmech/
 * transport — ONLY ever rendered on the overview chapter
 * (`SpeciesEntryPage.tsx` renders it exclusively for `activeSection ===
 * "overview"`; the product chapters render `EntryThermoSection`/
 * `EntryStatmechSection`/`EntryTransportSection` instead, which read the
 * full deposited records rather than a boolean summary).
 */
export function AvailabilitySection({ entry }: { entry: SpeciesEntryProjection }) {
    return <section className="product-manifest" aria-labelledby="manifest-title">
        <p className="eyebrow">Computed products</p>
        <h2 id="manifest-title">What this entry has on record</h2>
        <p>
            Deposited, not derived: each card names a scientific product this entry either has, or genuinely
            does not — an absent product reads as "unavailable", never as a failed lookup.
        </p>
        <div className="manifest-grid">
            {(["thermo", "statmech", "transport"] as const).map((path) => <Availability
                key={path}
                label={sectionLabels[path]}
                available={availabilityFor(entry, path)}
                path={path}
                entryRef={entry.species_entry_ref}
            />)}
        </div>
    </section>
}

function availabilityFor(entry: SpeciesEntryProjection, path: "thermo" | "statmech" | "transport") {
    if (path === "thermo") return entry.availability.has_thermo
    if (path === "statmech") return entry.availability.has_statmech
    return entry.availability.has_transport
}

function Availability({ label, available, path, entryRef }: {
    label: string
    available: boolean
    path: "thermo" | "statmech" | "transport"
    entryRef: string
}) {
    return <article className={`manifest-card${available ? " is-available" : " is-empty"}`}>
        <p className="eyebrow">{label}</p>
        <strong>{available ? "Available in this entry" : "Unavailable in this entry"}</strong>
        {available
            ? <Link to={`/species-entries/${entryRef}/${path}`}>View record section</Link>
            : <p>No {label.toLowerCase()} record is projected for this entry.</p>}
    </article>
}
