import { words } from "./provenanceFormat"

/**
 * Human labels for `EnergyCorrectionScheme.scheme_kind`
 * (`backend/app/db/models/common.py`'s `EnergyCorrectionSchemeKind`), moved
 * here from `LevelOfTheoryPage.tsx` (2026-09, live-rule-violation fix) so
 * `CorrectionSchemePage.tsx` can title a scheme from the SAME controlled
 * vocabulary instead of falling back to the depositor's own free-text
 * `name` -- see `schemeKindLabel` below for why that fallback existed and
 * why it is gone.
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
 * `bac_petersson`, verbatim) -- rendering it as a page heading is exactly
 * the "depositor label on a public page" the owner ruled out ("I kinda
 * don't want labels almost in general to never appear on the front end").
 * `CorrectionSchemePage.tsx`'s `<h1>` used to render `scheme.name`
 * directly; `LevelOfTheoryPage.tsx`'s own per-LOT scheme heading used
 * `SCHEME_KIND_LABELS[kind] ?? scheme.name` -- a fallback that never fired
 * for either of today's two schemes (both kinds ARE in the map) but stayed
 * a live path straight to depositor text the moment a future scheme kind
 * shipped without a label entry here. Falling back to `words(kind)`
 * instead keeps that same "never leave a kind unlabeled" intent without
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
 * row count -- "8 parameters" for `ecs_q5potmkzrmm6ynh2behv5kbfdu`
 * (atom-keyed), "45 parameters" for `ecs_5dzse4an2emubgyxge2dpj4ae4`
 * (bond-keyed), measured live.
 */
export function schemeParameterRollup(evidence: {
    atom_param_count: number
    bond_param_count: number
    component_param_count: number
}): string {
    const total = evidence.atom_param_count + evidence.bond_param_count + evidence.component_param_count
    return `${total} parameter${total === 1 ? "" : "s"}`
}
