/**
 * Shared correction-notice rendering for a superseded scientific record.
 * Used by the thermo / statmech / transport entry sections — every one of
 * those three record shapes carries the same `SupersessionNotice` fields
 * (`superseded_by`, `current`, `reason`, `superseded_at`, `chain_length`;
 * see `scientific_common.py`).
 *
 * A superseded record is never hidden — this renders alongside the rest of
 * the record's own data, not instead of it. `superseded_by` / `current` are
 * shown as plain `<code>` text, not links: none of the three record kinds
 * this project renders has a standalone `/thermo/:ref`-style detail route
 * (see the "Forbidden" note on `EntryThermoSection.tsx`), so a reader who
 * wants to follow the pointer has to search for it, not click it — that is
 * an honest limitation of this slice, not a broken link.
 */
export function SupersessionNotice({ supersession }: {
    supersession: {
        superseded_by: string
        current: string
        reason: string
        superseded_at: string
        chain_length: number
    }
}) {
    const supersededDate = supersession.superseded_at.slice(0, 10)
    return (
        <div className="supersession-notice">
            <strong>Superseded</strong>
            <p>
                This record was replaced by <code>{supersession.superseded_by}</code>.
                {supersession.chain_length > 1 && (
                    <> The current record in this chain is <code>{supersession.current}</code> ({supersession.chain_length} corrections since this one).</>
                )}
                {" "}Reason: {supersession.reason}. Superseded {supersededDate}.
            </p>
        </div>
    )
}
