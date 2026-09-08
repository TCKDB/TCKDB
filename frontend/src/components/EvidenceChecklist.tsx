import type { ReactNode } from "react"
import { Disclosure } from "./Disclosure"
import "../evidence-checklist.css"

/**
 * The ONE evidence/coverage/validation summary card every record page
 * renders now. Measured render sites (six routes, seven cards):
 * `/calculations` (`CalculationDetailPage.tsx`), `/conformer-observations`
 * (`ConformerObservationPage.tsx`), `/conformer-groups`
 * (`ConformerGroupPage.tsx`), `/geometries` (`GeometryDetailPage.tsx`),
 * `/reaction-entries` (`ReactionEntryPage.tsx`, two cards: its own
 * evidence checklist AND the per-kinetics-record "Evidence completeness"
 * card rendered inside `ReactionKineticsSection.tsx:228`), and
 * `/reactions` (`ReactionOverviewPage.tsx`, the cross-entry review-counts
 * card). `TransitionStateEntryPage.tsx` does NOT render this component
 * (an earlier version of this docstring claimed it did -- measured zero
 * instances; that page's own present/absent checklist is a different,
 * hand-built shape this component's pill CLASSES happen to match, not a
 * consumer of it). Every one of the real sites renders the SAME
 * structure: `.card.card--derived.coverage-card` > a `Disclosure`
 * (`components/Disclosure.tsx`) whose summary is the heading plus a
 * factual roll-up, and whose body is `.kv-list.coverage-checklist` (one
 * column, label above value, exactly `CalculationDetailPage`'s original
 * shape) > an optional `.note`. Its own stylesheet is
 * `evidence-checklist.css`, imported directly by this file (not left to
 * whichever page happens to import `conformer-group.css`) -- the same "a
 * component owns its own CSS" convention `RecordIdentityHeader` /
 * `RefsDisclosure` / `EnergyDisplay` each already follow.
 *
 * Owner report this fixes ("box sizes are different and the 'Evidence
 * present on this observation' box is in line with the other boxes when I
 * thought it would be underneath them. It should be lists for the
 * evidence, nicely presented. CONSISTENCY."): the calculation page's own
 * evidence card already sat on its own full-width row, below any tile
 * row, with its facts as a going-down list — the other three pages
 * rendered a one-line inline sentence sitting IN THE SAME grid row as
 * their metric tiles instead, at a different height and a different
 * shape. This component is that ONE shape, extracted so a fifth page
 * cannot reintroduce a sixth variant.
 *
 * Collapsible (owner: "Evidence blocks should be expandable rather" — the
 * reaction-entry page alone renders six always-open rows and runs ~7000px
 * tall). Built on the shared `Disclosure` primitive rather than a new
 * mechanism, so it inherits the one canonical `<details>`/`<summary>`
 * chrome the rest of the app already uses. **Collapsed by default**
 * (`Disclosure`'s own `defaultOpen` contract: read once, on mount; the
 * reader's own click/keyboard toggle owns `open` after that) -- WHEN a
 * true roll-up is available. The collapsed `<summary>` carries the
 * heading PLUS a factual roll-up of the rows so a reader who never opens
 * the card still learns whether evidence exists, computed one of two
 * ways:
 *
 * - Rows that carry a `tone` (a bounded-vocabulary status checklist):
 *   the roll-up is COMPUTED from each row's own `tone` (never a
 *   hardcoded string) as "N present, M absent" -- `tone: "pill"` rows
 *   count as "present", `tone: "pill-muted"` rows as "absent".
 * - Rows with no tone at all (a plain count list, e.g.
 *   `ConformerGroupPage`'s "N of M observations" rows, or a review-counts
 *   card): "present/absent" has no meaning for these, so the CALLER must
 *   supply its own true roll-up via the `summary` prop (e.g. "4 joined
 *   records", "3 of 3 stages covered") -- computed by the caller from the
 *   same data the rows themselves come from, never restated from the row
 *   count. **There is no numeric fallback here.** An earlier version of
 *   this component collapsed behind a bare `"N rows"` count when no
 *   `summary` was supplied -- MEASURED (independent review) that number
 *   is actively misleading: it is the number of CATEGORY rows (fixed per
 *   card shape) restated as if it answered the heading's own question
 *   ("Joined-record review counts" collapsing to "6 rows" while the real
 *   total was 4; a conformer group's coverage collapsing to the SAME "3
 *   rows" whether every stage is fully covered or none is at all -- two
 *   opposite evidence states rendered identically). When no `summary` is
 *   supplied and no tone-derived roll-up can be computed, this component
 *   renders the card OPEN (`defaultOpen={true}`) instead of collapsing it
 *   behind nothing meaningful -- a reader sees the real rows rather than
 *   a number that answers a different question than the one asked.
 *
 * `heading` is the box's own `.t-label` — always specific to what the
 * page is summarizing ("Evidence on this calculation" / "Evidence on this
 * geometry" / ...; one pattern across all four pages, post-review), never
 * restated a second time as a row label unless the row genuinely names a
 * different fact.
 *
 * A row's `value` is plain text ONLY for a count ("4 of 4 observations") --
 * every bounded-vocabulary status word is a pill, on every page, with no
 * exception (owner decision, post-review of 2bd17511: the calculation
 * page's own "absent"/"not applicable"/outcome words used to render as
 * plain text, the one place on the app this rule didn't reach; that was
 * an inconsistency this component exists to remove, not a case to
 * preserve). `tone: "pill"` renders a positive/neutral report ("present",
 * "recorded", an actual outcome like "passed"/"stable") as the plain
 * `.value-pill`; `tone: "pill-muted"` renders the absent/negative case
 * ("absent", "not applicable", "not recorded") as `.value-pill
 * .value-pill--muted` (design-system.css) — the SAME classes
 * `TransitionStateEntryPage.tsx`'s own present/absent checklist already
 * uses. `data-component="evidence-checklist"` on the root marks every
 * instance of this component in the DOM, so a page-level test can assert
 * "this card came from the shared component" without depending on any
 * one page's own class names or copy.
 *
 * A row's optional `to` links its value to wherever that thing actually
 * lives on the SAME page (e.g. `"#kinetics-heading"`, matching the plain
 * `<a href="#...">` convention `ReactionKineticsSection.tsx`'s own
 * network-only sentence already uses — no react-router `Link`, no route
 * change, just an in-page anchor). **The invariant this component itself
 * enforces is narrower than "presence links, absence doesn't": `to` is
 * only ever honoured when `tone === "pill"`; it is silently ignored on a
 * `pill-muted` row or a toneless row, even if a caller passes it.** `tone`
 * is the CALLER's own claim about the row, not something this component
 * derives from `value` -- so what is actually guaranteed is "a row the
 * caller marked `pill-muted` never links", not "a row that is factually
 * an absence never links" (nothing in this codebase mismarks one today;
 * this is a description of the mechanism, not a claim about every
 * caller's data). A caller is responsible for setting `tone: "pill"`
 * ONLY on a row that truly asserts presence, and for omitting `to`
 * entirely when a presence-asserting row has nowhere on the page to
 * point to — this component never invents a target.
 */
export type EvidenceChecklistTone = "pill" | "pill-muted"

export type EvidenceChecklistRow = {
    label: string
    value: ReactNode
    tone?: EvidenceChecklistTone
    to?: string
}

/**
 * The tone-derived collapsed-summary roll-up, e.g. "3 present, 2 absent"
 * — computed from each row's own `tone`, never a fixed string. Returns
 * `null` when no roll-up can be computed this way (no row carries a
 * tone), so the caller of THIS function can tell "nothing to show" apart
 * from a real "0 present, 0 absent" — collapsing `null` into that string
 * is exactly the `"N rows"` defect this replaced (see this component's
 * own docstring): a fixed fallback number that answers a different
 * question than the heading asks. A caller whose rows carry no tone must
 * supply the `EvidenceChecklist` `summary` prop with its own true
 * roll-up instead; this function has no fallback of its own.
 */
function summarizeRows(rows: EvidenceChecklistRow[]): string | null {
    const present = rows.filter((row) => row.tone === "pill").length
    const absent = rows.filter((row) => row.tone === "pill-muted").length
    if (present + absent === 0) return null
    return `${present} present, ${absent} absent`
}

function RowValue({ row }: { row: EvidenceChecklistRow }) {
    if (!row.tone) return <>{row.value}</>
    const className = row.tone === "pill-muted" ? "value-pill value-pill--muted" : "value-pill"
    // Only a row the CALLER marked `tone: "pill"` may ever link -- see
    // this component's own docstring for the exact invariant (`tone` is
    // the caller's claim, not something this component verifies).
    if (row.tone === "pill" && row.to) {
        return <a className={className} href={row.to}>{row.value}</a>
    }
    return <span className={className}>{row.value}</span>
}

export function EvidenceChecklist({ heading, rows, note, summary }: {
    heading: ReactNode
    rows: EvidenceChecklistRow[]
    note?: ReactNode
    /**
     * A true roll-up for a checklist whose rows carry no `tone` at all
     * (so `summarizeRows` cannot compute one) — e.g. "4 joined records"
     * for a review-counts card, or "3 of 3 stages covered" for a
     * per-stage coverage card. Computed by the CALLER from the same
     * underlying data the rows themselves come from (never the row
     * count) — see this component's own docstring for the defect this
     * fixes. Ignored when `summarizeRows` already found a tone-derived
     * roll-up (a toned checklist's roll-up is always the computed one).
     */
    summary?: ReactNode
}) {
    const rollup = summarizeRows(rows) ?? summary ?? null
    // No roll-up at all (no tone-derived one, and the caller supplied no
    // `summary`) -- open by default rather than collapsing behind
    // nothing meaningful. See this component's own docstring: this is
    // what replaced the misleading `"N rows"` fallback.
    const defaultOpen = rollup === null
    return (
        <div className="card card--derived coverage-card" data-component="evidence-checklist">
            <Disclosure
                summary={(
                    <>
                        <span className="t-label">{heading}</span>
                        {rollup !== null && <span className="coverage-checklist-summary">{rollup}</span>}
                    </>
                )}
                defaultOpen={defaultOpen}
            >
                <dl className="kv-list coverage-checklist">
                    {rows.map((row, index) => (
                        <div key={`${row.label}-${index}`}>
                            <dt>{row.label}</dt>
                            <dd><RowValue row={row} /></dd>
                        </div>
                    ))}
                </dl>
                {note && <p className="note">{note}</p>}
            </Disclosure>
        </div>
    )
}
