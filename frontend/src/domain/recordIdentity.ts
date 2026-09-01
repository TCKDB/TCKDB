/**
 * Normalizes the several page-specific "who owns this record" shapes
 * (`GeometryIdentity` on `/geometries/{ref}`, `CalculationOwnerSummary`
 * on `/calculations/{ref}`, the species entry's own fields) into one
 * small union `RecordIdentityHeader.tsx` knows how to render.
 *
 * Three absences this module keeps textually distinct, per the brief:
 *
 * - `{ kind: "species_entry" | "transition_state_entry", ... }` -- a
 *   known, unambiguous owner. A transition-state owner NEVER carries
 *   `canonicalSmiles`/`inchiKey` fields (the backend does not serve
 *   them for a TS -- see `GeometryTransitionStateIdentity`'s own
 *   docstring), so this type has no such fields to accidentally read as
 *   empty strings.
 * - `{ kind: "ambiguous", owners }` -- reachable from more than one
 *   distinct owner (only possible for a geometry, which is deduped by
 *   content hash and can be shared). Never guesses which owner is
 *   "the" one.
 * - `{ kind: "absent" }` -- no owner at all.
 */

export type AmbiguousIdentityOwner = { kind: string; ref: string }

export type SpeciesIdentity = {
    kind: "species_entry"
    formula: string | null
    canonicalSmiles: string
    inchiKey: string
    charge: number
    multiplicity: number
    speciesRef?: string
    speciesEntryRef?: string
    speciesEntryLabel?: string | null
}

export type TransitionStateIdentity = {
    kind: "transition_state_entry"
    formula: string | null
    unmappedSmiles: string | null
    charge: number
    multiplicity: number
    transitionStateRef?: string
    transitionStateEntryRef?: string
    label?: string | null
}

export type RecordIdentity =
    | SpeciesIdentity
    | TransitionStateIdentity
    | { kind: "ambiguous"; owners: AmbiguousIdentityOwner[] }
    | { kind: "absent" }

/** The served formula for a known (non-ambiguous, non-absent) identity, or `null`. */
export function identityFormula(identity: RecordIdentity): string | null {
    return identity.kind === "species_entry" || identity.kind === "transition_state_entry" ? identity.formula : null
}

// ---------------------------------------------------------------------------
// Geometry's `identity` block (`GeometryIdentity` on the wire) --
// see `api/geometryApi.ts`.
// ---------------------------------------------------------------------------

export type GeometryIdentityWire = {
    kind?: "species_entry" | "transition_state_entry" | null
    species_entry?: {
        species_ref: string
        species_entry_ref: string
        species_entry_label?: string | null
        formula?: string | null
        canonical_smiles: string
        inchi_key: string
        charge: number
        multiplicity: number
    } | null
    transition_state_entry?: {
        transition_state_ref: string
        transition_state_entry_ref: string
        formula?: string | null
        unmapped_smiles?: string | null
        charge: number
        multiplicity: number
    } | null
    ambiguous_owners?: AmbiguousIdentityOwner[]
}

export function identityFromGeometry(identity: GeometryIdentityWire | null | undefined): RecordIdentity {
    if (!identity) return { kind: "absent" }
    // `kind` is `null` when the geometry is ambiguous, and can also be
    // `undefined` if a future response omits it while still populating
    // `ambiguous_owners` -- both are treated the same way here (neither
    // owner sub-object can be trusted), never distinguished from a
    // guessed default.
    if (identity.kind == null) {
        const owners = identity.ambiguous_owners ?? []
        if (owners.length > 0) return { kind: "ambiguous", owners }
        return { kind: "absent" }
    }
    if (identity.kind === "species_entry" && identity.species_entry) {
        const entry = identity.species_entry
        return {
            kind: "species_entry",
            formula: entry.formula ?? null,
            canonicalSmiles: entry.canonical_smiles,
            inchiKey: entry.inchi_key,
            charge: entry.charge,
            multiplicity: entry.multiplicity,
            speciesRef: entry.species_ref,
            speciesEntryRef: entry.species_entry_ref,
            speciesEntryLabel: entry.species_entry_label,
        }
    }
    if (identity.kind === "transition_state_entry" && identity.transition_state_entry) {
        const entry = identity.transition_state_entry
        return {
            kind: "transition_state_entry",
            formula: entry.formula ?? null,
            unmappedSmiles: entry.unmapped_smiles ?? null,
            charge: entry.charge,
            multiplicity: entry.multiplicity,
            transitionStateRef: entry.transition_state_ref,
            transitionStateEntryRef: entry.transition_state_entry_ref,
        }
    }
    return { kind: "absent" }
}

// ---------------------------------------------------------------------------
// Calculation's `owner` block (`CalculationOwnerSummary` on the wire) --
// see `api/calculationApi.ts`. A calculation's owner is never ambiguous
// (the `one_owner` schema invariant), so this always resolves to a known
// owner or `absent` -- never `ambiguous`.
// ---------------------------------------------------------------------------

export type CalculationOwnerWire = {
    kind: "species_entry" | "transition_state_entry"
    species_entry?: {
        species_ref: string
        species_entry_ref: string
        species_entry_label?: string | null
        canonical_smiles: string
        inchi_key: string
        charge: number
        multiplicity: number
    } | null
    transition_state_entry?: {
        transition_state_ref: string
        transition_state_entry_ref: string
        label?: string | null
        charge: number
        multiplicity: number
    } | null
}

export function identityFromCalculationOwner(owner: CalculationOwnerWire | null | undefined): RecordIdentity {
    if (!owner) return { kind: "absent" }
    if (owner.kind === "species_entry" && owner.species_entry) {
        const entry = owner.species_entry
        return {
            kind: "species_entry",
            // Not served on this endpoint's owner summary -- see
            // `SpeciesEntryOwnerSummary`, which has no `formula` field.
            // Left `null` rather than guessed from `canonical_smiles`.
            formula: null,
            canonicalSmiles: entry.canonical_smiles,
            inchiKey: entry.inchi_key,
            charge: entry.charge,
            multiplicity: entry.multiplicity,
            speciesRef: entry.species_ref,
            speciesEntryRef: entry.species_entry_ref,
            speciesEntryLabel: entry.species_entry_label,
        }
    }
    if (owner.kind === "transition_state_entry" && owner.transition_state_entry) {
        const entry = owner.transition_state_entry
        return {
            kind: "transition_state_entry",
            formula: null,
            unmappedSmiles: null,
            charge: entry.charge,
            multiplicity: entry.multiplicity,
            transitionStateRef: entry.transition_state_ref,
            transitionStateEntryRef: entry.transition_state_entry_ref,
            label: entry.label,
        }
    }
    return { kind: "absent" }
}
