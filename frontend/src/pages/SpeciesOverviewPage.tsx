import { Link, useParams } from "react-router-dom"
import "../species-overview.css"
import type { SpeciesOverview } from "../api/speciesOverviewApi"
import type { ScientificSpeciesEntrySummary } from "../api/scientificSpeciesSchemas"
import { Disclosure } from "../components/Disclosure"
import { Formula } from "../components/Formula"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { facetChips } from "../domain/recordFacets"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { useSpeciesOverview } from "../hooks/useSpeciesOverview"

function token(value: string) {
    return value.replaceAll("_", " ")
}

function stateLabel(state: string) {
    return `${token(state)} electronic state`
}

function groupEntriesByState(entries: ScientificSpeciesEntrySummary[]) {
    return entries.reduce<Map<string, ScientificSpeciesEntrySummary[]>>((groups, entry) => {
        const grouped = groups.get(entry.electronic_state_kind)
        if (grouped) {
            grouped.push(entry)
        } else {
            groups.set(entry.electronic_state_kind, [entry])
        }
        return groups
    }, new Map())
}

export default function SpeciesOverviewPage() {
    const { speciesRef = "" } = useParams<{ speciesRef: string }>()
    const state = useSpeciesOverview(speciesRef)

    if (state.speciesRef !== speciesRef || state.status === "loading") {
        return <State title="Loading species record…" busy />
    }
    if (state.status === "missing") return <State title="Species not found" ref={speciesRef} />
    if (state.status === "rate-limited") {
        return (
            <State
                title="Archive is busy"
                ref={speciesRef}
                alert
                message={`The archive is receiving too many requests right now. Wait ${formatWaitSeconds(state.retryAfterSeconds)} and reload the page.`}
            />
        )
    }
    if (state.status !== "ready") {
        const title = state.status === "malformed" ? "Species data could not be read" : "Species unavailable"
        return <State title={title} alert />
    }
    return <SpeciesDocument species={state.species} />
}

function State({ title, ref, busy, alert, message }: {
    title: string
    ref?: string
    busy?: boolean
    alert?: boolean
    message?: string
}) {
    const resolvedMessage = message ?? (busy
        ? "Retrieving the species identity and its electronic-state entries."
        : alert
            ? "The archive response could not be read. Try again later."
            : "No species with this stable reference is available in this archive projection.")
    return (
        <section className="record-placeholder" aria-busy={busy} role={alert ? "alert" : undefined}>
            <p className="eyebrow">Archive record</p>
            <h1>{title}</h1>
            {ref && <code>{ref}</code>}
            <p>{resolvedMessage}</p>
        </section>
    )
}

function SpeciesDocument({ species }: { species: SpeciesOverview }) {
    const entryGroups = groupEntriesByState(species.entries)
    return (
        <section className="species-overview">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Species</span>
            </nav>
            <PageShell>
            <header className="species-header">
                <p className="eyebrow">Species record · chemical identity</p>
                {/* `formula` typeset with subscripts when the archive computed
                    one, matching `IdentifierSearch.tsx`'s headline rule; the
                    SMILES fallback is not chemistry-formula text, so it is
                    never run through `Formula`. */}
                <h1>{species.formula ? <Formula value={species.formula} /> : species.canonical_smiles}</h1>
                <dl className="kv-list species-identity-grid">
                    <Identity identifier wide label="Species ref" value={species.species_ref} />
                    <Identity identifier label="SMILES" value={species.canonical_smiles} />
                    <Identity identifier wide label="InChIKey" value={species.inchi_key} />
                    <Identity
                        label="Charge / multiplicity"
                        value={`${chargeDisplay(species.charge)} / ${spinDisplay(species.multiplicity)}`}
                    />
                </dl>
            </header>
            <section className="entry-index" aria-labelledby="electronic-state-entries">
                {/* Was two headings saying the same thing, stacked (an
                    "eyebrow" reading "State-specific records" directly
                    above an <h2> reading "Electronic-state entries") --
                    the owner quoted this exact nesting as noise. One
                    heading carries it; the eyebrow pattern elsewhere on
                    this page pairs a CATEGORY with a more specific
                    heading ("Species record · chemical identity" above
                    "CH3"), which this pairing never was.

                    The section used to ALSO show its own entry count here
                    ("N entries") right beside a per-state-group count just
                    below ("N entry"/"N entries" on each `<h3>`) -- for a
                    species with one group the two counts read the same
                    number twice for the same fact. The per-group count is
                    the more useful of the two (it says how many records
                    share a given state, which the section-level total
                    cannot), so it is the one that survives; this heading
                    no longer repeats it. */}
                <div className="entry-index-heading">
                    <SectionHeading id="electronic-state-entries">Electronic-state entries</SectionHeading>
                </div>
                {/* Single explanatory sentence for the whole section. This
                    used to be THREE: this paragraph, an near-identical
                    "select the entry you mean" sentence in the page header,
                    and a per-state-group "Each row is a separate record..."
                    sentence repeated inside every `EntryStateGroup`'s
                    `<details>`. The owner flagged three paragraphs
                    explaining one row as noise; this is the one that stays,
                    because it is the only one that explains the grouping
                    itself (why records with the same state classification
                    are not merged) rather than restating "read the row". */}
                <p className="entry-index-intro">
                    Entries are separate deposited records, grouped by electronic state so that repeated
                    ground-state records stay distinct without reading as interchangeable duplicates. Each row links
                    to its own state-specific record.
                </p>
                {species.entries.length ? (
                    <ul className="entry-state-groups">
                        {[...entryGroups].map(([state, entries]) => (
                            <EntryStateGroup entries={entries} key={state} state={state} />
                        ))}
                    </ul>
                ) : (
                    <p className="empty-projection">
                        No electronic-state entries are currently projected for this species.
                    </p>
                )}
            </section>
            </PageShell>
        </section>
    )
}

function EntryStateGroup({
    entries,
    state,
}: {
    entries: ScientificSpeciesEntrySummary[]
    state: string
}) {
    const groupId = `entry-state-${state}`
    const count = entries.length
    return (
        <li className="entry-state-group">
            <h3 id={groupId}>
                {stateLabel(state)}
                <span>{count} {count === 1 ? "entry" : "entries"}</span>
            </h3>
            <Disclosure defaultOpen summary="Deposited records" summaryProps={{ "aria-describedby": groupId }}>
                <ul className="entry-rows">
                    {entries.map((entry) => (
                        <EntryCard entry={entry} groupHeadingId={groupId} key={entry.species_entry_ref} />
                    ))}
                </ul>
            </Disclosure>
        </li>
    )
}

// `identifier` values (a species ref, a SMILES string, an InChIKey) render
// through `<code>` so the shared `.kv-list dd code` rule (design-system.css)
// picks them up at the data step -- mono, non-reflowing. Charge/multiplicity
// is a formatted phrase, not a raw identifier, so it stays plain text at the
// list's own value step.
//
// `wide` (post-review, PR D): the species ref and InChIKey are long enough
// (30+ characters) that at narrow widths -- where `.kv-list`'s `auto-fit`
// grid settles on 3 columns for these 4 pairs -- they wrapped mid-token
// inside a ~12rem cell. `.kv-list--wide` spans the pair across the full row
// instead, the same primitive an unusually long value anywhere else in a
// `.kv-list` would reach for. SMILES is left in the narrow column: shorter
// on this species and, unlike the other two, not itself the thing the
// reader copy-pastes to look this record up elsewhere.
function Identity({ label, value, identifier, wide }: { label: string; value: string; identifier?: boolean; wide?: boolean }) {
    return <div className={wide ? "kv-list--wide" : undefined}><dt>{label}</dt><dd>{identifier ? <code>{value}</code> : value}</dd></div>
}

/**
 * No pill boxes -- the heading used to render this entry's classification
 * as a row of `RecordFacetChips` pills, ALONGSIDE a separate `entry-facts`
 * `<dl>` that repeated the same State/Stereochemistry facts as labelled
 * rows right below it. The owner's complaint: two representations of the
 * same info stacked on one card. This card now states each fact exactly
 * once -- the heading is plain text built from the same raw axes the
 * pills used to read (`domain/recordFacets.ts`), so the "bare R" bug this
 * replaced (a heading collapsing to the single character "R" for an entry
 * distinguished only by stereochemistry) stays fixed: the heading is never
 * `species_entry_label` (a compact SERVER discriminator, not free text --
 * see that module's docstring) and always includes kind + state.
 *
 * `groupHeadingId` points at the enclosing `EntryStateGroup`'s own `<h3>`
 * ("ground electronic state", "excited electronic state") -- since every
 * card here is always rendered inside exactly one such group, the state
 * portion of the heading phrase is redundant by construction and is
 * dropped (`facetChips(entry, { includeState: false })`), while an
 * `aria-describedby` back to that heading keeps the state programmatically
 * associated with the card for a reader who lands on it directly (a
 * fragment link, assistive-tech heading navigation) without having
 * visually scrolled past the group heading first. `groupHeadingId` is
 * optional and the state-drop only happens when it is supplied, so a
 * hypothetical future caller that renders this card OUTSIDE a state group
 * gets the full, unabridged phrase automatically -- nothing here assumes
 * grouping will always exist.
 */
function EntryCard({ entry, groupHeadingId }: { entry: ScientificSpeciesEntrySummary; groupHeadingId?: string }) {
    const available = [
        entry.availability.has_conformers && "conformers",
        entry.availability.has_thermo && "thermo",
        entry.availability.has_statmech && "statmech",
        entry.availability.has_transport && "transport",
    ].filter(Boolean)

    const chips = facetChips(entry, { includeState: groupHeadingId === undefined ? true : false })
    const heading = chips.join(" · ")

    return (
        <li>
            <article aria-describedby={groupHeadingId} className="entry-card">
                <div className="entry-card-heading">
                    <h4>
                        <Link to={`/species-entries/${entry.species_entry_ref}`}>{heading}</Link>
                    </h4>
                    <span className="value-pill value-pill--muted">{token(entry.review.status)}</span>
                </div>
                <dl className="kv-list entry-facts">
                    <div><dt>Calculations</dt><dd>{entry.availability.calculation_count}</dd></div>
                    <div>
                        <dt>Available data</dt>
                        <dd>{available.length ? available.join(" · ") : "None projected"}</dd>
                    </div>
                </dl>
                {/* Used to also render a second, "Open state-specific record
                    →" link to this same destination, right below the
                    heading link above -- the owner flagged the row as
                    linking twice to the same page. The heading link is the
                    one that survives: it already carries the row's
                    accessible name, so a second link added no reachable
                    destination, only a second stop for anyone tabbing
                    through the row. */}
                <code className="data">{entry.species_entry_ref}</code>
            </article>
        </li>
    )
}
