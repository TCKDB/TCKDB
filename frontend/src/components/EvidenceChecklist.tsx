import type { ReactNode } from "react"
import "../evidence-checklist.css"

/**
 * The ONE evidence/coverage/validation summary card every record page
 * renders now — `/calculations`, `/conformer-observations`,
 * `/conformer-groups`, `/geometries` all render the SAME structure:
 * `.card.card--derived.coverage-card` > `.t-label` heading >
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
 */
export type EvidenceChecklistTone = "pill" | "pill-muted"

export type EvidenceChecklistRow = {
    label: string
    value: ReactNode
    tone?: EvidenceChecklistTone
}

export function EvidenceChecklist({ heading, rows, note }: {
    heading: ReactNode
    rows: EvidenceChecklistRow[]
    note?: ReactNode
}) {
    return (
        <div className="card card--derived coverage-card" data-component="evidence-checklist">
            <span className="t-label">{heading}</span>
            <dl className="kv-list coverage-checklist">
                {rows.map((row, index) => (
                    <div key={`${row.label}-${index}`}>
                        <dt>{row.label}</dt>
                        <dd>
                            {row.tone
                                ? (
                                    <span className={row.tone === "pill-muted" ? "value-pill value-pill--muted" : "value-pill"}>
                                        {row.value}
                                    </span>
                                )
                                : row.value}
                        </dd>
                    </div>
                ))}
            </dl>
            {note && <p className="note">{note}</p>}
        </div>
    )
}
