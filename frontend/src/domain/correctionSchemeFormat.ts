import { words } from "./provenanceFormat"

/**
 * Human labels for `EnergyCorrectionScheme.scheme_kind`
 * (`backend/app/db/models/common.py`'s `EnergyCorrectionSchemeKind`), shared
 * by `LevelOfTheoryPage.tsx` and `CorrectionSchemePage.tsx`, which title a
 * scheme from this controlled vocabulary instead of the depositor's own
 * free-text `name` -- see `schemeKindLabel` below.
 */
export const SCHEME_KIND_LABELS: Record<string, string> = {
    atom_energy: "Atom-energy correction",
    bac_petersson: "Petersson bond-additivity correction",
    bac_melius: "Melius bond-additivity correction",
    atom_hf: "Atomic enthalpy of formation",
    atom_thermal: "Atomic thermal contribution",
    soc: "Spin-orbit correction",
}

/**
 * A scheme's title, from the archive's own controlled vocabulary only --
 * never `scheme.name`. `energy_correction_scheme.name` is depositor free
 * text (the create schema accepts any string; on the live archive both
 * seeded rows happen to equal their own `scheme_kind`, `atom_energy`/
 * `bac_petersson`, verbatim) -- rendering it as a page heading would put a
 * depositor label on a public page. A scheme kind without a label entry
 * here falls back to `words(kind)` so no kind is left unlabeled, without
 * ever reading `name` -- e.g. a hypothetical unmapped `foo_bar` kind prints
 * "foo bar", not whatever free text a depositor happened to send.
 */
export function schemeKindLabel(kind: string): string {
    return SCHEME_KIND_LABELS[kind] ?? words(kind)
}

/**
 * The factual roll-up a collapsed correction-scheme box carries in its own
 * summary line (`LevelOfTheoryPage.tsx`'s per-scheme `Disclosure`) --
 * total deposited parameter rows, computed from the SAME
 * `evidence_summary` counts the open table itself renders from, never a
 * restated category count. Exists for the same reason
 * `EvidenceChecklist.tsx`'s own `summarizeRows`/`summary` contract does
 * (see that component's docstring): a collapsed box that answers nothing
 * -- or answers a fixed, misleading number -- defeats the point of
 * collapsing it. A live scheme here is atom-XOR-bond-XOR-component (never
 * two of the three at once), so the sum is exactly the one table's own
 * row count.
 */
export function schemeParameterRollup(evidence: {
    atom_param_count: number
    bond_param_count: number
    component_param_count: number
}): string {
    const total = evidence.atom_param_count + evidence.bond_param_count + evidence.component_param_count
    return `${total} parameter${total === 1 ? "" : "s"}`
}
