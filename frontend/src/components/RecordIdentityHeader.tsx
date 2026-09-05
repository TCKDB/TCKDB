import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import "../record-identity-header.css"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { facetChips } from "../domain/recordFacets"
import type { EntryFacetAxes } from "../domain/recordFacets"
import type { RecordIdentity } from "../domain/recordIdentity"

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
 * a kicker + status pill row, then the h1, then identity, then
 * classification facets, then provenance -- top to bottom, always in
 * that order, never reordered per page. See the design brief's "Shared
 * header block on every record page".
 *
 * `kicker`/`title` own the top of the block (design/foundations PR B):
 * every record page used to build its own eyebrow-row/h1 markup with
 * its own font-size rule (`.record-title h1`, `.basin-title h1`,
 * `.tse-equation-heading`, each a different clamp), which is what this
 * header now owns centrally at the shared `--type-display-1` step (or
 * `--type-display-2` for `TransitionStateEntryPage`'s wrapping reaction
 * equation -- pass `titleVariant="display-2"`). `pill` is the record's
 * ONE status/trust pill (a `.value-pill`/`.value-pill--muted` element the
 * caller builds) -- never more than one; a second badge (e.g. a trust
 * verdict) is the caller's to omit or fold in, not this header's to
 * stack.
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
 *
 * `identity`/`facets`/`submissionRef` render as `.kv-list`s (the shared
 * design-system primitive) rather than this header's own bespoke grid --
 * a caller's OWN provenance `.kv-list` (level of theory, software, ...)
 * and its `RefsDisclosure` still render below this component, in the
 * same page markup as before; this header only owns the identity tier
 * of that shared order, not every tier.
 */
export function RecordIdentityHeader({
    kicker, pill, title, titleVariant = "display-1", intro,
    identity, facets, submissionRef, explainTransitionStateIdentity = true,
}: {
    kicker: ReactNode
    pill?: ReactNode
    title: ReactNode
    titleVariant?: "display-1" | "display-2"
    /** SHOULD-FIX-7 (PR B review): an optional descriptive sentence
     *  between the h1 and the identity tier -- the same slot/role
     *  `SectionHeading`'s own `intro` prop plays for an in-page section,
     *  reused here so the canonical order (kicker -> h1 -> intro ->
     *  identity -> provenance -> References) is the SAME on every record
     *  page, not just the three that composed a page-local `<p>` for it
     *  in three different positions relative to identity. Rendered as
     *  `--type-body` capped to `--measure-prose`, same as `SectionHeading`'s. */
    intro?: ReactNode
    identity: RecordIdentity
    facets?: EntryFacetAxes
    submissionRef?: string | null
    explainTransitionStateIdentity?: boolean
}) {
    return (
        <div className="record-identity-header">
            <div className="record-identity-kicker-row">
                <span className="t-kicker record-identity-kicker">{kicker}</span>
                {pill}
            </div>
            <h1 className={titleVariant === "display-2" ? "t-display-2 record-identity-title" : "t-display-1 record-identity-title"}>
                {title}
            </h1>
            {intro && <p className="t-body section-intro">{intro}</p>}
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
            {facets && <p className="t-value record-identity-facets">{facetChips(facets).join(" · ")}</p>}
            {submissionRef !== undefined && (
                <dl className="kv-list record-identity-provenance">
                    <div>
                        <dt>Submission</dt>
                        <dd>
                            {submissionRef
                                ? <code>{submissionRef}</code>
                                : <span className="record-identity-absent-inline">not recorded</span>}
                        </dd>
                    </div>
                </dl>
            )}
        </div>
    )
}

function IdentityTier({ identity, explainTransitionStateIdentity }: {
    identity: RecordIdentity
    explainTransitionStateIdentity: boolean
}) {
    if (identity.kind === "absent") {
        return <p className="note record-identity-absent">No molecular identity is recorded for this record.</p>
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
                            <span className="t-label">{owner.kind.replaceAll("_", " ")}</span>
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
                {/* No standalone formula paragraph here any more (design/
                    foundations PR B): every caller of this header already
                    renders the same formula (or the canonical SMILES
                    fallback) as the record's own `title`/h1 above, via
                    `Formula`/`identity.formula` -- a second, large serif
                    restatement of it here duplicated the page's own title
                    immediately beneath it. The full identity facts
                    (SMILES, InChIKey, charge/multiplicity) still render
                    below unchanged. */}
                <dl className="kv-list record-identity-facts">
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
    // No standalone formula paragraph here either (see the species_entry
    // branch's own comment above): a geometry owned by a transition state
    // that DOES carry a served formula (`GeometryTransitionStateIdentity`,
    // unlike the calculation-owner shape) already renders it as this
    // header's own `title`/h1 (`GeometryDetailPage`'s `displayFormula`),
    // so a second serif restatement here duplicated it immediately below.
    return (
        <div className="record-identity-known">
            {explainTransitionStateIdentity && (
                <p className="note record-identity-note">
                    Transition states have no canonical SMILES the way a species does; the unmapped SMILES below,
                    where deposited, is a depositor-supplied label, not a deduped identity key.
                </p>
            )}
            <dl className="kv-list record-identity-facts">
                {/* The producer's own label (e.g. "TS0") -- BLOCKING-1 fix
                    (PR B review): this used to be its own `.tse-label-facet`
                    span in `TransitionStateEntryPage.tsx`'s kicker row, a
                    class this stylesheet consolidation retired without
                    updating that page's markup to match, leaving an
                    unstyled 16px sans span next to an 11.5px pill. A plain
                    identity fact -- the same tier as charge/multiplicity
                    and the entry ref below -- needs no page-local class of
                    its own. Only rendered when the identity actually
                    carries a label: `GeometryDetailPage`'s TS-owned-
                    geometry identity (`GeometryTransitionStateIdentity`)
                    never serves this field, so this fact is silently
                    absent there rather than showing an empty row. */}
                {identity.label && (
                    <IdentityFact label="Label">{identity.label}</IdentityFact>
                )}
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
