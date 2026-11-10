import type { RecordKind } from "../components/RecordGlyph"
import type { NeighbourProbe } from "../api/neighbourApi"

/** One record or group of records beside the one the reader is on. */
export type NeighbourNode = {
    id: string
    kind: RecordKind
    /** What the node is, in plain words. */
    label: string
    /** How it relates to this entry. */
    relation: string
    /** The page it opens; `null` when it stands for several records and no single page is the right target. */
    to: string | null
    /** What the read returned, in words ("2 found"); never a guess. */
    found: string | null
}

export type ParticipantInput = { entryRef: string; label: string }

const record = (value: unknown): Record<string, unknown> | null =>
    value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null
const text = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null)

/** The count line for a group: "1 found" or "N found", straight from the read's own total. */
const foundText = (total: number) => `${total} found`

export type SpeciesEntryProbes = {
    molecule: NeighbourProbe | null
    conformers: NeighbourProbe | null
    calculations: NeighbourProbe | null
    networks: NeighbourProbe | null
    reactions: NeighbourProbe | null
}

/**
 * Nodes for a species entry. A probe that failed (`null`) or found nothing leaves its node out. A group opens a
 * record page only when the read found exactly one record; otherwise it opens the section of this page that lists
 * them, if there is one, and is plain text if not.
 */
export function speciesEntryNodes(entryRef: string, probes: SpeciesEntryProbes): NeighbourNode[] {
    const nodes: NeighbourNode[] = []
    const speciesRef = text(probes.molecule?.row?.species_ref)
    if (probes.molecule && speciesRef) {
        nodes.push({
            id: "molecule", kind: "species", label: "The molecule", relation: "All its versions",
            to: `/species/${encodeURIComponent(speciesRef)}`, found: null,
        })
    }
    const geometrySection = `/species-entries/${encodeURIComponent(entryRef)}/geometry`
    if (probes.conformers && probes.conformers.total > 0) {
        const ref = text(record(probes.conformers.row?.conformer_group)?.conformer_group_ref)
        nodes.push({
            id: "conformers", kind: "conformer-group", label: "Conformer groups", relation: "Shapes of this entry",
            to: probes.conformers.total === 1 && ref ? `/conformer-groups/${encodeURIComponent(ref)}` : geometrySection,
            found: foundText(probes.conformers.total),
        })
    }
    if (probes.calculations && probes.calculations.total > 0) {
        const ref = text(record(probes.calculations.row?.calculation)?.calculation_ref)
        nodes.push({
            id: "calculations", kind: "calculation", label: "Calculations", relation: "Run on this entry",
            to: probes.calculations.total === 1 && ref ? `/calculations/${encodeURIComponent(ref)}` : geometrySection,
            found: foundText(probes.calculations.total),
        })
    }
    if (probes.networks && probes.networks.total > 0) {
        const ref = text(record(probes.networks.row?.network)?.network_ref)
        nodes.push({
            id: "networks", kind: "network", label: "Networks", relation: "Pressure-dependent networks with this entry",
            to: probes.networks.total === 1 && ref ? `/networks/${encodeURIComponent(ref)}` : null,
            found: foundText(probes.networks.total),
        })
    }
    if (probes.reactions && probes.reactions.total > 0) {
        const ref = text(probes.reactions.row?.reaction_entry_ref)
        nodes.push({
            id: "reactions", kind: "reaction", label: "Reactions with this molecule", relation: "Either side of the equation",
            to: probes.reactions.total === 1 && ref ? `/reaction-entries/${encodeURIComponent(ref)}` : null,
            found: foundText(probes.reactions.total),
        })
    }
    return nodes
}

/** Nodes for a reaction entry, from the data its page already holds: participants in deposit order, then the reaction. */
export function reactionEntryNodes(input: {
    reactionRef: string | null
    reactants: ParticipantInput[]
    products: ParticipantInput[]
}): NeighbourNode[] {
    const participant = (role: "reactant" | "product") => (p: ParticipantInput, index: number): NeighbourNode => ({
        id: `${role}-${index}`,
        kind: "species-entry",
        label: p.label,
        relation: role === "reactant" ? "Reactant" : "Product",
        to: `/species-entries/${encodeURIComponent(p.entryRef)}`,
        found: null,
    })
    const nodes = [...input.reactants.map(participant("reactant")), ...input.products.map(participant("product"))]
    if (input.reactionRef) {
        nodes.push({
            id: "reaction", kind: "reaction", label: "The reaction", relation: "All entries for this reaction",
            to: `/reactions/${encodeURIComponent(input.reactionRef)}`, found: null,
        })
    }
    return nodes
}
