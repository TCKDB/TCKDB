import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import "../record-identity-header.css"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { facetChips } from "../domain/recordFacets"
import type { EntryFacetAxes } from "../domain/recordFacets"
import type { RecordIdentity } from "../domain/recordIdentity"
import { Formula } from "./Formula"

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
 */
export function RecordIdentityHeader({ identity, facets, submissionRef }: {
    identity: RecordIdentity
    facets?: EntryFacetAxes
    submissionRef?: string | null
}) {
    return (
        <div className="record-identity-header">
            <IdentityTier identity={identity} />
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

function IdentityTier({ identity }: { identity: RecordIdentity }) {
    if (identity.kind === "absent") {
        return <p className="record-identity-absent">No molecular identity is recorded for this record.</p>
    }
    if (identity.kind === "ambiguous") {
        return (
            <div className="record-identity-ambiguous" role="status" data-testid="record-identity-ambiguous">
                <p>
                    This record is reachable from more than one distinct owner. Rather than guess, the
                    identity below is left unresolved -- see the owner list to disambiguate by calculation.
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
    // no field that could render as an empty "SMILES" row.
    return (
        <div className="record-identity-known">
            <p className="record-identity-formula">
                {identity.formula ? <Formula value={identity.formula} /> : (identity.label ?? "Transition state")}
            </p>
            <p className="record-identity-note">
                Transition states have no canonical SMILES the way a species does; the unmapped SMILES below,
                where deposited, is a depositor-supplied label, not a deduped identity key.
            </p>
            <dl className="record-identity-facts">
                <IdentityFact label="Unmapped SMILES">
                    {identity.unmappedSmiles ? <code>{identity.unmappedSmiles}</code> : <span className="record-identity-absent-inline">not recorded</span>}
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

function IdentityFact({ label, children }: { label: string; children: ReactNode }) {
    return <div><dt>{label}</dt><dd>{children}</dd></div>
}
