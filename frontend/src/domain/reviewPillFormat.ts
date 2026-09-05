/**
 * The one canonical pill rule every record page follows (item 6, design/
 * foundations PR B "record pages" consolidation): accent `.value-pill`
 * for a positive/settled review state, `.value-pill value-pill--muted`
 * for an absent/not-yet-reviewed one. Review status only ever has one
 * "absent" reading -- `not_reviewed` -- so that is the only status this
 * maps to the muted variant; every other status (a record that HAS been
 * reviewed, whatever the verdict) is the plain accent pill.
 *
 * NIT fix (PR B review): this was duplicated verbatim in
 * `CalculationDetailPage.tsx`, `ConformerGroupPage.tsx`,
 * `ConformerObservationPage.tsx` and `TransitionStateEntryPage.tsx` --
 * one small, easy-to-drift copy per file. One helper here instead, same
 * convention every other cross-page formatter in this directory
 * (`chemistryFormat.ts`, `provenanceFormat.ts`, ...) already follows.
 */
export function reviewPillClass(status: string): string {
    return status === "not_reviewed" ? "value-pill value-pill--muted" : "value-pill"
}
