/**
 * The short label of a level of theory, its qualifier pieces, and the rule
 * that keeps two different levels from reading alike on one page.
 * Everything is built from the structured fields the reads return; the
 * server's long `label` is only used when no structured field reached the view.
 */
export type LevelFields = {
    method: string
    basis?: string | null
    label?: string | null
    display?: string
    level_of_theory_ref?: string
    dispersion?: string | null
    solvent?: string | null
    solvent_model?: string | null
    aux_basis?: string | null
    cabs_basis?: string | null
    core_treatment?: string | null
    spin_treatment?: string | null
}

type QualifierKey = "aux_basis" | "cabs_basis" | "dispersion" | "solvent" | "solvent_model" | "spin_treatment" | "core_treatment"

export type Qualifier = { key: QualifierKey; name: string; value: string }

/** Order the pieces are listed in, with the name each is shown under. */
const QUALIFIERS: readonly { key: QualifierKey; name: string }[] = [
    { key: "aux_basis", name: "aux" },
    { key: "cabs_basis", name: "cabs" },
    { key: "dispersion", name: "dispersion" },
    { key: "solvent", name: "solvent" },
    { key: "solvent_model", name: "solvent model" },
    { key: "spin_treatment", name: "spin" },
    { key: "core_treatment", name: "core" },
]

/** Stored enum tokens read as words; free text (a basis, a dispersion name) keeps its stored spelling. */
const TOKEN_FIELDS: ReadonlySet<QualifierKey> = new Set(["spin_treatment", "core_treatment", "solvent_model"])

function stated(value: string | null | undefined): value is string {
    return value !== null && value !== undefined && value.trim() !== ""
}

/** The stated qualifiers of a level, as short labelled pieces. */
export function qualifiersOf(level: LevelFields): Qualifier[] {
    return QUALIFIERS.flatMap(({ key, name }) => {
        const raw = level[key]
        return stated(raw) ? [{ key, name, value: TOKEN_FIELDS.has(key) ? raw.replaceAll("_", " ") : raw }] : []
    })
}

/** "method/basis" as stored. */
export function baseLabel(level: LevelFields): string {
    return level.basis ? `${level.method}/${level.basis}` : level.method
}

/** Stable identity of a level's qualifiers: two levels with the same base label and signature are the same level. */
export function qualifierSignature(level: LevelFields): string {
    return qualifiersOf(level).map((q) => `${q.key}=${q.value}`).join("|")
}

export type LevelKey = string
export const levelKey = (level: LevelFields): LevelKey => `${baseLabel(level)}\u0000${qualifierSignature(level)}`

/** What prints for one qualifier inside the short label. */
export const inlineText = (q: Qualifier): string => (q.key === "core_treatment" || q.key === "spin_treatment" ? q.value : `${q.name} ${q.value}`)

/**
 * For every distinct level among `levels`, the qualifiers to print inline.
 * Levels that share a base label but differ in qualifiers get the qualifiers
 * that differ (a level that lacks one prints nothing for it); every other
 * level gets none.
 */
export function inlineQualifiers(levels: readonly LevelFields[]): Map<LevelKey, Qualifier[]> {
    const byBase = new Map<string, Map<LevelKey, LevelFields>>()
    for (const level of levels) {
        const group = byBase.get(baseLabel(level)) ?? new Map<LevelKey, LevelFields>()
        group.set(levelKey(level), level)
        byBase.set(baseLabel(level), group)
    }
    const result = new Map<LevelKey, Qualifier[]>()
    for (const group of byBase.values()) {
        if (group.size < 2) continue
        const members = [...group.entries()].map(([key, level]) => ({ key, pieces: qualifiersOf(level) }))
        const differing = QUALIFIERS.filter(({ key }) => new Set(members.map((m) => m.pieces.find((p) => p.key === key)?.value ?? null)).size > 1)
        for (const member of members) result.set(member.key, member.pieces.filter((p) => differing.some((d) => d.key === p.key)))
    }
    return result
}
