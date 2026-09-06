import type { ReactNode } from "react"

/**
 * The ONE evidence/coverage/validation summary card every record page
 * renders now — `/calculations`, `/conformer-observations`,
 * `/conformer-groups`, `/geometries` all render the SAME structure:
 * `.card.card--derived.coverage-card` > `.t-label` heading >
 * `.kv-list.coverage-checklist` (one column, label above value, exactly
 * `CalculationDetailPage`'s original shape) > an optional `.note`.
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
 * page is summarizing ("Evidence on this calculation" / "Observation
 * coverage" / ...), never restated a second time as a row label unless
 * the row genuinely names a different fact.
 *
 * A row's `value` is plain text (a count, a sentence) unless `tone` is
 * given: `tone: "pill"` / `"pill-muted"` renders it as the site-wide
 * `.value-pill` / `.value-pill--muted` pair (`design-system.css`) —
 * the SAME classes `TransitionStateEntryPage.tsx`'s own present/absent
 * checklist already uses for a bounded-vocabulary status word ("present"
 * vs "absent", "recorded" vs "not recorded"). A count ("4 of 4
 * observations") is not a status word and stays plain text — see the
 * per-page call sites for which is which. `CalculationDetailPage`'s own
 * two rows (`geometryValidationOutcomeLabel`/`scfStabilityOutcomeLabel`)
 * pass no `tone` at all, preserving that page's pre-existing rendered
 * output exactly (this component is a pure extraction there, not a
 * redesign — see this PR's DOM-comparison test).
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
        <div className="card card--derived coverage-card">
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
