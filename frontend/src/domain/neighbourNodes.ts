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
    /** The node's own reference, kept whole here; the drawing shortens it and every text form shows it in full. */
    ref?: string
    /** The page or section it opens; `null` only for a failed read, or when nothing lists the records yet (the node then says so). */
    to: string | null
    /** A short second line in plain words ("optimisation, b3lyp/def2tzvp"); shown when the node has no ref of its own. */
    sub?: string
    /** The second line of the drawing only, when it must say more than the list does ("Reactant · [CH2]C=C"). */
    drawnNote?: string
    /** What the read returned, in words ("2 found"); never a guess. */
    found: string | null
}

/** A reaction participant: only codes and values (formula, SMILES, ref); a depositor-written label is never read. */
export type ParticipantInput = { entryRef: string; formula?: string | null; smiles?: string | null }

const record = (value: unknown): Record<string, unknown> | null =>
    value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null
const text = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null)

/** The count line for a group: "1 found" or "N found", straight from the read's own total. */
const foundText = (total: number) => `${total} found`

/** The reactions listing narrowed to this molecule on either side; `null` when its SMILES is not known. */
const reactionsWithMolecule = (smiles: string | null): string | null =>
    smiles ? `/reactions?reactant_smiles=${encodeURIComponent(smiles)}&direction=either` : null

export type SpeciesEntryProbes = {
    molecule: NeighbourProbe | null
    conformers: NeighbourProbe | null
    calculations: NeighbourProbe | null
    networks: NeighbourProbe | null
    reactions: NeighbourProbe | null
}

const UNAVAILABLE = "Not available right now"

type ProbeName = keyof SpeciesEntryProbes
const FAILED_NODES: Record<ProbeName, Pick<NeighbourNode, "kind" | "label" | "relation">> = {
    molecule: { kind: "species", label: "The molecule", relation: "All its versions" },
    conformers: { kind: "conformer-group", label: "Conformer groups", relation: "Shapes of this entry" },
    calculations: { kind: "calculation", label: "Calculations", relation: "Run on this entry" },
    networks: { kind: "network", label: "Networks", relation: "Pressure-dependent networks with this entry" },
    reactions: { kind: "reaction", label: "Reactions with this molecule", relation: "Either side of the equation" },
}

/**
 * Nodes for a species entry. A probe that found nothing leaves its node out; one that failed (`failed`) stays as plain
 * text saying it is not available. A group opens its record's page when the read found exactly one; otherwise it opens
 * the place that lists them: conformer groups the geometry section, calculations the per-shape calculations section of
 * this page, reactions the reactions listing filtered to this molecule's SMILES. Several networks have no list, so
 * that node is plain text and says "not listed yet".
 */
export function speciesEntryNodes(
    entryRef: string,
    probes: SpeciesEntryProbes,
    failed: ReadonlySet<ProbeName> = new Set(),
    smiles: string | null = null,
): NeighbourNode[] {
    const nodes: NeighbourNode[] = []
    const geometrySection = `/species-entries/${encodeURIComponent(entryRef)}/geometry`
    const unavailable = (name: ProbeName) =>
        nodes.push({ id: name, ...FAILED_NODES[name], to: null, found: UNAVAILABLE })

    if (failed.has("molecule")) unavailable("molecule")
    else {
        const speciesRef = text(probes.molecule?.row?.species_ref)
        if (probes.molecule && speciesRef) {
            nodes.push({
                id: "molecule", ...FAILED_NODES.molecule, to: `/species/${encodeURIComponent(speciesRef)}`, found: null,
            })
        }
    }
    if (failed.has("conformers")) unavailable("conformers")
    else if (probes.conformers && probes.conformers.total > 0) {
        const ref = text(record(probes.conformers.row?.conformer_group)?.conformer_group_ref)
        nodes.push({
            id: "conformers", ...FAILED_NODES.conformers,
            to: probes.conformers.total === 1 && ref ? `/conformer-groups/${encodeURIComponent(ref)}` : geometrySection,
            found: foundText(probes.conformers.total),
        })
    }
    if (failed.has("calculations")) unavailable("calculations")
    else if (probes.calculations && probes.calculations.total > 0) {
        // The page lists calculations only under the selected shape, and it selects one whenever the entry has any.
        const listed = !!probes.conformers && probes.conformers.total > 0 && !failed.has("conformers")
        nodes.push({
            id: "calculations", ...FAILED_NODES.calculations, to: listed ? "#shape-calculations" : null,
            found: `${foundText(probes.calculations.total)}, ${listed ? "listed by shape" : "not listed yet"}`,
        })
    }
    if (failed.has("networks")) unavailable("networks")
    else if (probes.networks && probes.networks.total > 0) {
        const ref = text(record(probes.networks.row?.network)?.network_ref)
        nodes.push({
            id: "networks", ...FAILED_NODES.networks,
            to: probes.networks.total === 1 && ref ? `/networks/${encodeURIComponent(ref)}` : null,
            found: probes.networks.total === 1 && ref ? foundText(1) : `${foundText(probes.networks.total)}, not listed yet`,
        })
    }
    if (failed.has("reactions")) unavailable("reactions")
    else if (probes.reactions && probes.reactions.total > 0) {
        const ref = text(probes.reactions.row?.reaction_entry_ref)
        const total = probes.reactions.total
        nodes.push({
            id: "reactions", ...FAILED_NODES.reactions,
            to: total === 1 && ref ? `/reaction-entries/${encodeURIComponent(ref)}` : reactionsWithMolecule(smiles),
            found: `${total} reaction ${total === 1 ? "entry" : "entries"}`,
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
        label: p.formula || p.smiles || p.entryRef,
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
