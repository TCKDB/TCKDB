import { words } from "./provenanceFormat"

/**
 * The four orthogonal axes that make one species entry chemically
 * different from another of the same species (see the five columns of
 * `uq_species_entry_species_id` in `backend/app/schemas/reads/
 * scientific_species.py` — this is those minus `species_entry_id`
 * itself). `isotope_key` is the served field for the isotopologue axis;
 * the backend explicitly does not serve a separate `isotopologue_label`.
 */
export type EntryFacetAxes = {
    species_entry_kind: string
    electronic_state_kind: string
    electronic_state_label?: string | null
    term_symbol?: string | null
    stereo_label?: string | null
    isotope_key?: string | null
}

const KIND_LABELS: Record<string, string> = {
    minimum: "minimum",
    vdw_complex: "van der Waals complex",
}

function kindChip(kind: string): string {
    return KIND_LABELS[kind] ?? words(kind) ?? kind
}

function stateChip(kind: string, label?: string | null, termSymbol?: string | null): string {
    const base = kind === "ground" ? "ground state" : kind === "excited" ? "excited state" : `${words(kind) ?? kind} state`
    const extra = [label, termSymbol].filter((part): part is string => Boolean(part)).join(" · ")
    return extra ? `${base} · ${extra}` : base
}

// Exported: reused by `SpeciesEntrySummary.tsx`'s `EntryIdentity` to print
// a labelled "Stereochemistry" row (e.g. "E isomer", not a bare "E") now
// that the pill row this text used to live in only is gone -- see that
// component's own comment for the report this fixes ("why ... does it not
// show ... E isomer like it does for Review etc.").
export function stereoChip(label: string): string {
    if (label === "R" || label === "S") return `${label} enantiomer`
    if (label === "E" || label === "Z") return `${label} isomer`
    return label
}

function isotopeChip(key: string): string {
    return `isotopologue ${key}`
}

/**
 * One chip of text per axis that is actually set, replacing the single
 * collapsed heading string a species entry used to resolve as:
 * `species_entry_label ?? electronic_state_label ?? "kind · state"`
 * (`SpeciesOverviewPage.tsx`'s `EntryCard`, before this module existed).
 *
 * That fallback chain is not what produced the "bare R" bug it looks
 * like it should explain. `species_entry_label` on the wire is not free
 * text a curator typed — it is computed server-side by
 * `app.services.scientific_read.species_identity.species_entry_label`
 * as a compact DISCRIMINATOR: every column that agrees with the
 * species' default (ground state, no term symbol, no isotope label) is
 * deliberately left OUT, so only what makes this entry different from
 * its siblings survives into the string. For an entry whose only
 * distinguishing feature is a stereo descriptor, that discriminator
 * really is just `"R"` — correct on the server's own terms. The bug is
 * entirely on this side: treating that discriminator as if it were a
 * complete heading silently drops "minimum · ground" instead of adding
 * to it.
 *
 * Chips close that gap structurally rather than by re-ordering the same
 * fallback: they are built ONLY from the four raw axes below and never
 * read `species_entry_label` at all, so there is nothing left that can
 * silently stand in for the whole classification. An axis that is unset
 * (no stereochemistry, no isotopologue) contributes no chip — never a
 * placeholder, never an empty pill.
 *
 * `includeState` (default `true`) exists for exactly one caller:
 * `SpeciesOverviewPage.tsx`'s `EntryCard`, grouped under a heading that
 * already names the shared electronic state for every card beneath it
 * ("ground electronic state", "excited electronic state") — repeating
 * "ground state" on every card in that group is redundant BY
 * CONSTRUCTION, not a fact to keep re-stating. `includeState: false`
 * drops the bare kind/state phrase but keeps any `electronic_state_label`
 * / `term_symbol` EXTRA that would otherwise have been folded into it —
 * those are not established by the group heading, so they must not
 * disappear along with the redundant part. The default stays `true` so
 * every other caller (and this function itself, if the grouping is ever
 * removed) keeps showing the full classification with nothing to opt into.
 */
export function facetChips(entry: EntryFacetAxes, options?: { includeState?: boolean }): string[] {
    const includeState = options?.includeState ?? true
    const chips = [kindChip(entry.species_entry_kind)]
    if (includeState) {
        chips.push(stateChip(entry.electronic_state_kind, entry.electronic_state_label, entry.term_symbol))
    } else {
        const extra = [entry.electronic_state_label, entry.term_symbol]
            .filter((part): part is string => Boolean(part))
            .join(" · ")
        if (extra) chips.push(extra)
    }
    if (entry.stereo_label) chips.push(stereoChip(entry.stereo_label))
    if (entry.isotope_key) chips.push(isotopeChip(entry.isotope_key))
    return chips
}
