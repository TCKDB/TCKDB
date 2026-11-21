import type { CatalogueCard, CatalogueCitation, LevelOfTheoryCatalogue, MethodCatalogueCard } from "../api/catalogueApi"

// Words and paths for the method and basis-set catalogue. A family or rung is a classification, never a measure of
// accuracy, so nothing here orders or grades an entry.

const METHOD_FAMILY_WORDS: Record<string, string> = {
    dft: "density functional",
    wavefunction: "wave function",
    composite: "composite",
    multireference: "multireference",
    semiempirical: "semiempirical",
    force_field: "force field",
    unclassified: "unclassified",
}

const DFT_RUNG_WORDS: Record<number, string> = {
    1: "LDA",
    2: "GGA",
    3: "meta-GGA",
    4: "hybrid",
    5: "double hybrid",
}

const BASIS_ROLE_WORDS: Record<string, string> = {
    orbital: "orbital basis",
    auxiliary_correlation: "auxiliary basis, correlation fitting",
    auxiliary_coulomb: "auxiliary basis, Coulomb fitting",
    cabs: "complementary auxiliary basis (CABS)",
}

const REFERENCE_ROLE_HEADINGS: Record<string, [one: string, many: string]> = {
    original: ["Original article", "Original articles"],
    component: ["Component", "Components"],
    implementation: ["Implementation", "Implementation"],
}

const plainWords = (token: string) => token.replaceAll("_", " ")

export function isMethodCard(card: CatalogueCard): card is MethodCatalogueCard {
    return "kind" in card
}

/** "density functional, hybrid", "semiempirical", "dispersion correction". */
export function methodFamilyText(card: MethodCatalogueCard): string {
    if (card.kind === "dispersion_correction") return "dispersion correction"
    if (!card.family) return "not classified"
    const family = METHOD_FAMILY_WORDS[card.family] ?? plainWords(card.family)
    const rung = card.dft_rung == null ? null : (DFT_RUNG_WORDS[card.dft_rung] ?? null)
    return rung ? `${family}, ${rung}` : family
}

/** "orbital basis", "auxiliary basis, correlation fitting", ... */
export function basisRoleText(role: string): string {
    return BASIS_ROLE_WORDS[role] ?? plainWords(role)
}

/** The family line for any card: a method's classification, or a basis set's family and role. */
export function cardFamilyText(card: CatalogueCard): string {
    return isMethodCard(card) ? methodFamilyText(card) : `${card.family} · ${basisRoleText(card.role)}`
}

/** What a level-of-theory slot holds, for the card heading: "About this method", "About this basis set", ... */
export type CatalogueSlot = keyof Pick<LevelOfTheoryCatalogue, "method" | "dispersion" | "basis" | "aux_basis" | "cabs_basis">

export const CATALOGUE_SLOTS: readonly CatalogueSlot[] = ["method", "dispersion", "basis", "aux_basis", "cabs_basis"]

const SLOT_NOUNS: Record<CatalogueSlot, string> = {
    method: "method",
    dispersion: "dispersion correction",
    basis: "basis set",
    aux_basis: "auxiliary basis set",
    cabs_basis: "CABS basis set",
}

export function slotNoun(slot: CatalogueSlot): string {
    return SLOT_NOUNS[slot]
}

/** Reference roles in the order they are listed, with the articles of each in citation order. */
export function referencesByRole(references: readonly CatalogueCitation[]): { role: string; heading: string; items: CatalogueCitation[] }[] {
    const groups = new Map<string, CatalogueCitation[]>()
    for (const reference of [...references].sort((a, b) => a.position - b.position)) {
        const group = groups.get(reference.role)
        if (group) group.push(reference)
        else groups.set(reference.role, [reference])
    }
    const known = Object.keys(REFERENCE_ROLE_HEADINGS)
    const roles = [...known.filter((role) => groups.has(role)), ...[...groups.keys()].filter((role) => !known.includes(role))]
    return roles.map((role) => {
        const items = groups.get(role) ?? []
        const [one, many] = REFERENCE_ROLE_HEADINGS[role] ?? [plainWords(role), plainWords(role)]
        return { role, heading: items.length === 1 ? one : many, items }
    })
}

/** "Bannwarth, Ehlert, Grimme"; more than six names end in "et al.". */
export function authorsText(authors: readonly string[]): string | null {
    if (authors.length === 0) return null
    return authors.length > 6 ? `${authors.slice(0, 6).join(", ")} et al.` : authors.join(", ")
}

/** "Journal of Chemical Theory and Computation 2019, 15, 1652–1671". */
export function sourceText(reference: CatalogueCitation): string | null {
    const head = [reference.journal, reference.year].filter((part) => part != null && part !== "").join(" ")
    const tail = [reference.volume, reference.pages?.replace(/(\d)-(\d)/, "$1–$2")].filter((part) => part != null && part !== "")
    const text = [head, ...tail].filter((part) => part !== "").join(", ")
    return text === "" ? null : text
}

/** A path segment the catalogue read accepts: the key when it fits in one segment, else the ref every card carries. */
function handleOf(card: { key: string; catalogue_ref: string }): string {
    return card.key.includes("/") ? card.catalogue_ref : card.key
}

export function methodCataloguePath(card: { key: string; catalogue_ref: string }): string {
    return `/methods/method/${encodeURIComponent(handleOf(card))}`
}

export function basisSetCataloguePath(card: { key: string; catalogue_ref: string }): string {
    return `/methods/basis/${encodeURIComponent(handleOf(card))}`
}

export function cataloguePath(card: CatalogueCard): string {
    return isMethodCard(card) ? methodCataloguePath(card) : basisSetCataloguePath(card)
}

/**
 * The line under a level of theory's heading, from its catalogue cards only: "GFN2-xTB · semiempirical",
 * "B3LYP / def2-TZVP · density functional, hybrid". `null` when the method has no entry.
 */
export function levelCatalogueSubtitle(catalogue: LevelOfTheoryCatalogue | null | undefined): string | null {
    const method = catalogue?.method
    if (!method) return null
    const names = [method.short_name, catalogue?.basis?.short_name].filter(Boolean).join(" / ")
    return `${names} · ${methodFamilyText(method)}`
}

export function usageText(usage: { level_usage_count: number; calculation_usage_count: number }): string {
    const levels = usage.level_usage_count === 1 ? "1 level of theory" : `${usage.level_usage_count} levels of theory`
    const calculations = usage.calculation_usage_count === 1 ? "1 calculation" : `${usage.calculation_usage_count} calculations`
    return `Used by ${levels} · ${calculations}`
}
