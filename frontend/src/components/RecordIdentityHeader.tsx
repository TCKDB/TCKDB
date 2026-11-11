import type { ReactNode } from "react"
import "../record-identity-header.css"
import { CopyButton } from "./RefsDisclosure"
import { SpeciesEntryLink } from "./SpeciesEntryLink"
import { RecordGlyph, type RecordKind } from "./RecordGlyph"
import { ReviewChip } from "./ReviewChip"
import { Lede } from "./Lede"
import type { LedeText } from "../domain/ledeText"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { facetChips } from "../domain/recordFacets"
import type { EntryFacetAxes } from "../domain/recordFacets"
import type { RecordIdentity } from "../domain/recordIdentity"

/**
 * The shared header block every record page (species entry, geometry,
 * calculation, conformer group, conformer observation) renders through:
 * a kicker + status pill row, then the h1, then identity, then
 * classification facets, then provenance -- top to bottom, always in
 * that order, never reordered per page.
 *
 * `kicker`/`title` own the top of the block: this header owns the
 * eyebrow-row/h1 markup of every record page at the shared
 * `--type-display-2` step.
 * `pill` is the record's
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
 * - `explainTransitionStateIdentity` (default `true`) governs the one note saying a transition
 *   state is identified by the reaction it connects, not by a SMILES. It is shown only for a
 *   `transition_state_entry` identity. `TransitionStateEntryPage` passes `false`; a TS-owned
 *   geometry (`GeometryDetailPage`) keeps the default.
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
 * never need a click to see. A caller supplying
 * `ownRef` is responsible for NOT also repeating the same ref in its own
 * `RefsDisclosure` list below -- see that component's own docstring for
 * the rule this splits: the record's own ref lives here, related refs
 * (parent species, entry, group, calculation, submission…) stay in the
 * collapsed disclosure.
 */
type OwnerProps =
    /** The owner's identity, shown after the record's own ref. */
    | { identity: RecordIdentity; ownFacts?: never }
    /** For a page whose owner context does not fit `RecordIdentity`: extra rows after the record's own ref. */
    | { identity?: never; ownFacts: ReactNode }

export function RecordIdentityHeader({
    kicker, pill, glyph, review, lede, title, intro,
    identity, facets, submissionRef, explainTransitionStateIdentity = true, ownRef, ownFacts,
}: OwnerProps & {
    kicker: ReactNode
    pill?: ReactNode
    /** The record kind's glyph, shown before the kicker. */
    glyph?: RecordKind
    /** The record's review status, shown as a `ReviewChip` beside the kicker. */
    review?: string
    /** The one sentence under the title, built with `ledeText`. When both `lede` and `intro` are given only `lede` renders. */
    lede?: LedeText
    /** A human label (a name, a formula or an equation), never the record's ref. */
    title: ReactNode
    /** An optional descriptive sentence
     *  between the h1 and the identity tier -- the same slot/role
     *  `SectionHeading`'s own `intro` prop plays for an in-page section,
     *  so the canonical order (kicker -> h1 -> intro ->
     *  identity -> provenance -> References) is the SAME on every record
     *  page. Rendered as
     *  `--type-body` capped to `--measure-prose`, same as `SectionHeading`'s. */
    intro?: ReactNode
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
    if (import.meta.env.DEV && lede && intro) {
        console.error("RecordIdentityHeader: pass either `lede` or `intro`, not both; only `lede` is shown.")
    }
    return (
        <div className="record-identity-header">
            <div className="record-identity-kicker-row">
                {glyph && <RecordGlyph kind={glyph} size={16} />}
                <span className="t-kicker record-identity-kicker">{kicker}</span>
                {review !== undefined && <ReviewChip status={review} />}
                {pill}
            </div>
            <h1 className="t-display-2 record-identity-title">
                {title}
            </h1>
            {lede ? <Lede>{lede}</Lede> : intro && <p className="t-body section-intro">{intro}</p>}
            {identity ? (
                <IdentityTier identity={identity} explainTransitionStateIdentity={explainTransitionStateIdentity} ownRef={ownRef} />
            ) : (
                <dl className="kv-list record-identity-facts">
                    {ownRef && (
                        <IdentityFact label={ownRef.label} copy={ownRef.value} own>
                            <code className="data">{ownRef.value}</code>
                        </IdentityFact>
                    )}
                    {ownFacts}
                </dl>
            )}
            {/* No pill boxes: a plain, readable phrase built from the raw
                facet axes. No caller of this header
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
        <IdentityFact label={ownRef.label} copy={ownRef.value} own>
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
                {/* No standalone formula paragraph here: every caller of this header already
                    renders the same formula (or the canonical SMILES
                    fallback) as the record's own `title`/h1 above, via
                    `Formula`/`identity.formula` -- a second, large serif
                    restatement of it here would duplicate the page's own title
                    immediately beneath it. The full identity facts
                    (SMILES, InChIKey, charge/multiplicity) render
                    below. */}
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
                                rather than re-deriving the same link text here: the entry's own formula (the SAME
                                `Formula` component the h1 uses, so subscripts match) when served,
                                falling back to the entry ref as `<code className="data">` -- never the literal
                                words "Species entry" (the `<dt>` beside this `<dd>` already says
                                "Species entry"). `species_entry_label` is never printed:
                                it joins in depositor-typed parts (see
                                `SpeciesEntryLink`). */}
                            <SpeciesEntryLink
                                speciesEntryRef={identity.speciesEntryRef}
                                smiles={identity.canonicalSmiles}
                                formula={identity.formula}
                                facets={identity.facets}
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
    // `TransitionStateEntryCoreBlock`); the reaction equation is a facet on
    // `TransitionStateEntryPage`'s own `<h1>` row rather than this
    // header's job to restate.
    //
    // The transition-state identity note is gated behind `explainTransitionStateIdentity` (default true);
    // `TransitionStateEntryPage` passes `false`, a TS-owned geometry keeps the default.
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
                    A transition state has no SMILES identity; it is identified by the reaction it connects.
                </p>
            )}
            <dl className="kv-list record-identity-facts">
                {ownRefFact}
                {/* The producer's own `transition_state.label` (e.g. "TS0") is not
                    shown: no depositor-typed labels appear on public pages, and an
                    arbitrary producer string tells a reader nothing (separate deposits of
                    the same saddle point can carry the identical label). The entry ref
                    below and, on the transition-state-entry page itself, the
                    reaction-deposit context distinguish the record; this header never
                    reads `identity.label` for display. */}
                {/* No unmapped SMILES: it is stored exactly as the depositor typed it, with no parsing
                    or canonicalisation, so no check on its shape can show it safely. The title
                    already carries the reaction equation. */}
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
 * than a second, bespoke button, so the affordance is available for every
 * identifier value the header
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
function IdentityFact({ label, children, wide, copy, own }: {
    label: string
    children: ReactNode
    wide?: boolean
    copy?: string
    own?: boolean
}) {
    return (
        <div className={wide ? "record-identity-fact-wide" : own ? "record-identity-fact-ref" : undefined}>
            <dt>{label}</dt>
            <dd className={copy ? "record-identity-fact-copyable" : undefined}>
                {children}
                {copy && <CopyButton value={copy} label={label} srLabel="value" />}
            </dd>
        </div>
    )
}
