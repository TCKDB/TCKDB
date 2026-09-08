import type { ReactNode } from "react"
import { Disclosure } from "./Disclosure"
import "../evidence-checklist.css"

/**
 * The ONE evidence/coverage/validation summary card every record page
 * renders now — `/calculations`, `/conformer-observations`,
 * `/conformer-groups`, `/geometries`, `/reaction-entries`, and the
 * kinetics-record card each render the SAME structure: `.card.card--
 * derived.coverage-card` > a `Disclosure` (`components/Disclosure.tsx`)
 * whose summary is the heading plus a factual roll-up, and whose body is
 * `.kv-list.coverage-checklist` (one column, label above value, exactly
 * `CalculationDetailPage`'s original shape) > an optional `.note`. Its own
 * stylesheet is `evidence-checklist.css`, imported directly by this file
 * (not left to whichever page happens to import `conformer-group.css`) --
 * the same "a component owns its own CSS" convention `RecordIdentityHeader`
 * / `RefsDisclosure` / `EnergyDisplay` each already follow.
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
 * reader's own click/keyboard toggle owns `open` after that). The
 * collapsed `<summary>` still carries the heading PLUS a factual roll-up
 * of the rows ("N present, M absent") so a reader who never opens the
 * card still learns whether evidence exists — the roll-up is COMPUTED
 * from each row's own `tone` (never a hardcoded string), counting
 * `tone: "pill"` rows as "present" and `tone: "pill-muted"` rows as
 * "absent"; a checklist whose rows carry no tone at all (a plain count
 * list, e.g. `ConformerGroupPage`'s "N of M observations" rows, or this
 * page's own "Review" card) falls back to a plain row count instead,
 * since "present/absent" has no meaning for a row that is not a
 * bounded-vocabulary status in the first place.
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
 * A row's optional `to` links a `tone: "pill"` (presence-asserting) row's
 * value to wherever that thing actually lives on the SAME page (e.g.
 * `"#kinetics-heading"`, matching the plain `<a href="#...">` convention
 * `ReactionKineticsSection.tsx`'s own network-only sentence already uses
 * — no react-router `Link`, no route change, just an in-page anchor).
 * **Never honoured on a `pill-muted` row, or a row with no tone at all,
 * even if a caller passes it anyway** — a link promises a destination; an
 * absence has none, and a link that scrolls nowhere is worse than plain
 * text. A caller whose presence-asserting row has nowhere on the page to
 * point to simply omits `to` — this component never invents a target.
 */
export type EvidenceChecklistTone = "pill" | "pill-muted"

export type EvidenceChecklistRow = {
    label: string
    value: ReactNode
    tone?: EvidenceChecklistTone
    to?: string
}

/**
 * The collapsed-summary roll-up text, e.g. "3 present, 2 absent" —
 * computed from each row's own `tone`, never a fixed string. Rows with no
 * tone at all (a plain count, not a bounded-vocabulary status) don't
 * count toward "present"/"absent" either way; when NONE of the rows carry
 * a tone, the roll-up falls back to a bare row count instead of a
 * meaningless "0 present, 0 absent".
 */
function summarizeRows(rows: EvidenceChecklistRow[]): string {
    const present = rows.filter((row) => row.tone === "pill").length
    const absent = rows.filter((row) => row.tone === "pill-muted").length
    if (present + absent === 0) {
        return rows.length === 1 ? "1 row" : `${rows.length} rows`
    }
    return `${present} present, ${absent} absent`
}

function RowValue({ row }: { row: EvidenceChecklistRow }) {
    if (!row.tone) return <>{row.value}</>
    const className = row.tone === "pill-muted" ? "value-pill value-pill--muted" : "value-pill"
    // Only a `tone: "pill"` (presence-asserting) row may ever link -- see
    // this component's own docstring for why `to` on a muted/toneless row
    // is silently ignored rather than honoured.
    if (row.tone === "pill" && row.to) {
        return <a className={className} href={row.to}>{row.value}</a>
    }
    return <span className={className}>{row.value}</span>
}

export function EvidenceChecklist({ heading, rows, note }: {
    heading: ReactNode
    rows: EvidenceChecklistRow[]
    note?: ReactNode
}) {
    return (
        <div className="card card--derived coverage-card" data-component="evidence-checklist">
            <Disclosure
                summary={(
                    <>
                        <span className="t-label">{heading}</span>
                        <span className="coverage-checklist-summary">{summarizeRows(rows)}</span>
                    </>
                )}
                defaultOpen={false}
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
