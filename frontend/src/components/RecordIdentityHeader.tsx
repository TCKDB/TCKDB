import type { ReactNode } from "react"
import "../record-identity-header.css"
import { CopyButton } from "./RefsDisclosure"
import { SpeciesEntryLink } from "./SpeciesEntryLink"
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
 *
 * `ownRef`: the record's OWN public ref (`calc_…`/`geom_…`/`spe_…`/
 * `cg_…`/`co_…`/`tse_…`) -- deliberately a SEPARATE prop from `identity`,
 * not a field folded into the `RecordIdentity` union. `identity` answers
 * "what molecular thing does this record belong to" (an OWNER, for a
 * calculation or geometry -- see that type's own docstring), which is a
 * different question from "what IS this record's own stable identifier",
 * and the two happen to coincide only for a page that IS its own
 * identity's subject (`SpeciesEntrySummary`, `TransitionStateEntryPage`).
 * Folding `ownRef` into `identity` would have made every OTHER caller
 * (calculation, geometry) carry a field with nothing to do with molecular
 * identity at all.
 *
 * Rendered as the FIRST fact in the identity tier, unconditionally --
 * before SMILES/InChIKey, before the "no molecular identity" note, even
 * before the ambiguous-owner list -- because it is the one fact that must
 * never need a click to see (the owner's decision this prop exists to
 * satisfy: "yes show each record's own ref inline"). A caller supplying
 * `ownRef` is responsible for NOT also repeating the same ref in its own
 * `RefsDisclosure` list below -- see that component's own docstring for
 * the rule this splits: the record's own ref lives here, related refs
 * (parent species, entry, group, calculation, submission…) stay in the
 * collapsed disclosure.
 */
export function RecordIdentityHeader({
    kicker, pill, title, titleVariant = "display-1", intro,
    identity, facets, submissionRef, explainTransitionStateIdentity = true, ownRef,
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
    /** The record's own public ref, shown first, always -- see this
     *  component's own docstring just above. `label` matches the label
     *  style already established per record kind (`"Observation ref"`,
     *  `"Group ref"`, `"Calculation ref"`, `"Geometry ref"`, …). Omitted
     *  entirely (not just left absent) when a caller has no own ref to
     *  offer -- there is no record page this header serves that lacks
     *  one, so in practice every caller supplies it. */
    ownRef?: { label: string; value: string }
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
            <IdentityTier identity={identity} explainTransitionStateIdentity={explainTransitionStateIdentity} ownRef={ownRef} />
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

function IdentityTier({ identity, explainTransitionStateIdentity, ownRef }: {
    identity: RecordIdentity
    explainTransitionStateIdentity: boolean
    ownRef?: { label: string; value: string }
}) {
    // Rendered first, in every branch, unconditionally -- see
    // `RecordIdentityHeader`'s own docstring on `ownRef` for why this
    // sits outside/above the per-identity-kind branching below rather
    // than being folded into any one branch's `dl`.
    const ownRefFact = ownRef && (
        <IdentityFact label={ownRef.label} copy={ownRef.value}>
            <code className="data">{ownRef.value}</code>
        </IdentityFact>
    )
    if (identity.kind === "absent") {
        return (
            <div className="record-identity-known">
                {ownRefFact && <dl className="kv-list record-identity-facts">{ownRefFact}</dl>}
                <p className="note record-identity-absent">No molecular identity is recorded for this record.</p>
            </div>
        )
    }
    if (identity.kind === "ambiguous") {
        return (
            <div className="record-identity-ambiguous" role="status" data-testid="record-identity-ambiguous">
                {ownRefFact && <dl className="kv-list record-identity-facts">{ownRefFact}</dl>}
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
                    {ownRefFact}
                    <IdentityFact label="SMILES" copy={identity.canonicalSmiles}><code>{identity.canonicalSmiles}</code></IdentityFact>
                    <IdentityFact label="InChIKey" copy={identity.inchiKey}><code>{identity.inchiKey}</code></IdentityFact>
                    <IdentityFact label="Charge / multiplicity">
                        {chargeDisplay(identity.charge)} / {spinDisplay(identity.multiplicity)}
                    </IdentityFact>
                    {identity.speciesEntryRef && (
                        <IdentityFact label="Species entry">
                            {/* Delegates to `SpeciesEntryLink` (`./SpeciesEntryLink.tsx`)
                                rather than re-deriving the same link text here --
                                the reviewer of #375 flagged this exact duplication
                                (this header, `ConformerGroupPage.tsx`, and
                                `ConformerObservationPage.tsx` each hand-rolling the
                                same formula-then-`stereoChip`-label logic with three
                                different fallbacks for "no formula served"). One
                                expression now: the entry's own formula (the SAME
                                `Formula` component the h1 uses, so subscripts
                                match) when served, falling back to the entry ref
                                as `<code className="data">` -- never the literal
                                words "Species entry" (an earlier version of this
                                header did that; the `<dt>` beside this `<dd>`
                                already says "Species entry", so repeating it as
                                the value said nothing a reader didn't already
                                have), and never the raw `speciesEntryLabel` string
                                alone as the sole text. See `SpeciesEntryLink`'s own
                                docstring for why `species_entry_label` is a
                                server-computed discriminator, not depositor free
                                text, and why it is expanded through `stereoChip`
                                rather than shown raw. */}
                            <SpeciesEntryLink
                                speciesEntryRef={identity.speciesEntryRef}
                                formula={identity.formula}
                                speciesEntryLabel={identity.speciesEntryLabel}
                            />
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
                {ownRefFact}
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
                <IdentityFact label="Reaction SMILES (unmapped)" wide copy={identity.unmappedSmiles ?? undefined}>
                    {identity.unmappedSmiles ? <code>{withSmilesBreaks(identity.unmappedSmiles)}</code> : <span className="record-identity-absent-inline">not recorded</span>}
                </IdentityFact>
                <IdentityFact label="Charge / multiplicity">
                    {chargeDisplay(identity.charge)} / {spinDisplay(identity.multiplicity)}
                </IdentityFact>
                {identity.transitionStateEntryRef && (
                    <IdentityFact label="Transition state entry" copy={identity.transitionStateEntryRef}><code>{identity.transitionStateEntryRef}</code></IdentityFact>
                )}
            </dl>
        </div>
    )
}

/**
 * `copy`, when given a truthy value, adds a small copy button beside the
 * fact's own rendered value -- reusing `RefsDisclosure`'s `CopyButton`
 * (same clipboard write / "Copied" feedback / aria-label pattern) rather
 * than a second, bespoke button (PR #372 dropped the species-entry
 * hero's old "Copy SMILES"/"Copy InChIKey" buttons when that hero moved
 * onto this shared header; only the ref copy button survived -- this
 * brings the affordance back for every identifier value the header
 * renders as `.data`: SMILES, InChIKey, an unmapped reaction SMILES, a
 * TS entry ref). Deliberately NOT wired for `children` generally -- only
 * a caller passing `copy` gets the button, so a plain fact like "Charge
 * / multiplicity" (not an identifier, nothing meaningful to copy alone)
 * stays exactly as it renders today. `undefined`/empty means nothing to
 * copy -- e.g. a transition-state identity's optional unmapped SMILES
 * before one is deposited -- and renders no button, matching the "not
 * recorded" placeholder those facts already show instead of an empty
 * value.
 */
function IdentityFact({ label, children, wide, copy }: {
    label: string
    children: ReactNode
    wide?: boolean
    copy?: string
}) {
    return (
        <div className={wide ? "record-identity-fact-wide" : undefined}>
            <dt>{label}</dt>
            <dd className={copy ? "record-identity-fact-copyable" : undefined}>
                {children}
                {copy && <CopyButton value={copy} label={label} srLabel="value" />}
            </dd>
        </div>
    )
}
