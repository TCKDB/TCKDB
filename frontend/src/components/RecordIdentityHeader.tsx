import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import "../record-identity-header.css"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { facetChips } from "../domain/recordFacets"
import type { EntryFacetAxes } from "../domain/recordFacets"
import type { RecordIdentity } from "../domain/recordIdentity"
import { Formula } from "./Formula"

/**
 * Breaks a SMILES-shaped string at `>>` (reaction arrow) and `.`
 * (disconnected-fragment separator) boundaries with `<wbr>` -- the
 * punctuation that already marks a sensible break point in this
 * vocabulary, so a long unmapped-SMILES string wraps at a token
 * boundary instead of `overflow-wrap: anywhere` picking an arbitrary
 * character mid-fragment.
 */
function withSmilesBreaks(value: string): ReactNode {
    const parts = value.split(/(>>|\.)/)
    const nodes: ReactNode[] = []
    parts.forEach((part, index) => {
        nodes.push(part)
        if (part === ">>" || part === ".") nodes.push(<wbr key={`smiles-wbr-${index}`} />)
    })
    return nodes
}

/**
 * The shared header block every record page (species entry, geometry,
 * calculation, conformer group, conformer observation) renders through:
 * identity, then classification facets, then provenance -- top to
 * bottom, always in that order, never reordered per page. See the
 * design brief's "Shared header block on every record page".
 *
 * Each tier renders only what the endpoint actually served:
 * - `identity` is required (it is what tells the ambiguous/absent/known
 *   cases apart -- see `domain/recordIdentity.ts`).
 * - `facets` is optional. Several endpoints this header is used on do
 *   not serve `species_entry_kind`/`electronic_state_kind` at all (the
 *   geometry and conformer-group/observation surfaces, as of this
 *   writing) -- omitting the prop there is the honest rendering, not a
 *   bug to work around client-side.
 * - `submissionRef` is `string | null | undefined` and the three states
 *   are NOT interchangeable: `undefined` means the wire key itself was
 *   absent (an anonymous caller was never told whether a submission
 *   exists) and renders NO row at all; `null` means an authenticated
 *   caller was told there is no linked submission, and renders "not
 *   recorded"; a string renders the ref.
 * - `explainTransitionStateIdentity` (default `true`) governs the one
 *   sentence explaining that a transition state has no canonical SMILES
 *   the way a species does -- shown only for a `transition_state_entry`
 *   identity. `TransitionStateEntryPage` passes `false`: its own Reaction
 *   section already states this (see that page's own comment), and
 *   showing the sentence twice ~900px apart on one page was the exact
 *   duplication this override exists to avoid. Every OTHER caller that
 *   can render a `transition_state_entry` identity but has no Reaction
 *   section of its own -- `GeometryDetailPage` on a TS-owned geometry, as
 *   of this writing -- keeps the default `true` and gets the one sentence
 *   this header has always carried for that case.
 */
export function RecordIdentityHeader({ identity, facets, submissionRef, explainTransitionStateIdentity = true }: {
    identity: RecordIdentity
    facets?: EntryFacetAxes
    submissionRef?: string | null
    explainTransitionStateIdentity?: boolean
}) {
    return (
        <div className="record-identity-header">
            <IdentityTier identity={identity} explainTransitionStateIdentity={explainTransitionStateIdentity} />
            {/* No pill boxes: a plain, readable phrase built from the same
                raw axes a pill row used to read one-per-pill -- see
                `SpeciesEntrySummary.tsx`'s `EntryIdentity` for the report
                this pattern fixes elsewhere. No caller of this header
                currently supplies `facets` at all (the geometry/conformer
                surfaces this header serves today don't carry these axes on
                the wire), so this line has no live duplication to worry
                about yet -- it exists so a future caller that does supply
                `facets` starts from the readable shape, not the pill one. */}
            {facets && <p className="record-identity-facets">{facetChips(facets).join(" · ")}</p>}
            {submissionRef !== undefined && (
                <p className="record-identity-provenance">
                    <span className="record-identity-provenance-label">Submission</span>
                    {submissionRef
                        ? <code>{submissionRef}</code>
                        : <span className="record-identity-absent-inline">not recorded</span>}
                </p>
            )}
        </div>
    )
}

function IdentityTier({ identity, explainTransitionStateIdentity }: {
    identity: RecordIdentity
    explainTransitionStateIdentity: boolean
}) {
    if (identity.kind === "absent") {
        return <p className="record-identity-absent">No molecular identity is recorded for this record.</p>
    }
    if (identity.kind === "ambiguous") {
        return (
            <div className="record-identity-ambiguous" role="status" data-testid="record-identity-ambiguous">
                <p>
                    This record is reachable from more than one distinct owner. Rather than guess, the
                    identity below is left unresolved — see the owner list to disambiguate by calculation.
                </p>
                <ul className="record-identity-ambiguous-owners">
                    {identity.owners.map((owner) => (
                        <li key={`${owner.kind}-${owner.ref}`}>
                            <span className="record-identity-provenance-label">{owner.kind.replaceAll("_", " ")}</span>
                            <code>{owner.ref}</code>
                        </li>
                    ))}
                </ul>
            </div>
        )
    }
    if (identity.kind === "species_entry") {
        return (
            <div className="record-identity-known">
                <p className="record-identity-formula">
                    {identity.formula ? <Formula value={identity.formula} /> : identity.canonicalSmiles}
                </p>
                <dl className="record-identity-facts">
                    <IdentityFact label="SMILES"><code>{identity.canonicalSmiles}</code></IdentityFact>
                    <IdentityFact label="InChIKey"><code>{identity.inchiKey}</code></IdentityFact>
                    <IdentityFact label="Charge / multiplicity">
                        {chargeDisplay(identity.charge)} / {spinDisplay(identity.multiplicity)}
                    </IdentityFact>
                    {identity.speciesEntryRef && (
                        <IdentityFact label="Species entry">
                            <Link to={`/species-entries/${identity.speciesEntryRef}`}>
                                {identity.speciesEntryLabel ?? identity.speciesEntryRef}
                            </Link>
                        </IdentityFact>
                    )}
                </dl>
            </div>
        )
    }
    // transition_state_entry -- deliberately no "SMILES" or "InChIKey"
    // row here at all: a transition state has neither the way a species
    // does (see `TransitionStateIdentity`'s own docstring), so there is
    // no field that could render as an empty "SMILES" row. Likewise no
    // formula slot: a TS never carries `formula` on this endpoint (see
    // `TransitionStateEntryCoreBlock`), and the label this used to fall
    // back to is now a facet on `TransitionStateEntryPage`'s own `<h1>`
    // row (the reaction equation, per the h1 rework) rather than this
    // header's job to restate.
    //
    // The "no canonical SMILES" note is gated behind
    // `explainTransitionStateIdentity` (default true). It used to
    // duplicate, almost word for word, `TransitionStateEntryPage`'s own
    // Reaction-section lede ("A transition state is identified by the
    // reaction it connects, not a molecular graph of its own.") -- the
    // two sentences sat ~900px apart on the same page saying the same
    // thing. That page passes `false` and keeps its own lede as the one
    // explanation; every other caller (a TS-owned geometry on
    // `GeometryDetailPage`, which has no Reaction section of its own)
    // keeps the default and still gets this sentence.
    return (
        <div className="record-identity-known">
            {identity.formula && (
                <p className="record-identity-formula">
                    <Formula value={identity.formula} />
                </p>
            )}
            {explainTransitionStateIdentity && (
                <p className="record-identity-note">
                    Transition states have no canonical SMILES the way a species does; the unmapped SMILES below,
                    where deposited, is a depositor-supplied label, not a deduped identity key.
                </p>
            )}
            <dl className="record-identity-facts">
                <IdentityFact label="Reaction SMILES (unmapped)" wide>
                    {identity.unmappedSmiles ? <code>{withSmilesBreaks(identity.unmappedSmiles)}</code> : <span className="record-identity-absent-inline">not recorded</span>}
                </IdentityFact>
                <IdentityFact label="Charge / multiplicity">
                    {chargeDisplay(identity.charge)} / {spinDisplay(identity.multiplicity)}
                </IdentityFact>
                {identity.transitionStateEntryRef && (
                    <IdentityFact label="Transition state entry"><code>{identity.transitionStateEntryRef}</code></IdentityFact>
                )}
            </dl>
        </div>
    )
}

function IdentityFact({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
    return <div className={wide ? "record-identity-fact-wide" : undefined}><dt>{label}</dt><dd>{children}</dd></div>
}
