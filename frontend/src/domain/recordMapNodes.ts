import type { RecordKind } from "../components/RecordGlyph"
import type { NeighbourNode, ParticipantInput } from "./neighbourNodes"

/**
 * Nodes for the "Where this record sits" map on every record page except the species entry, built only from what the
 * page has already loaded. A node whose data the page does not hold is left out, never drawn as a placeholder; a
 * count opens the page's own section when it has one.
 */

const enc = encodeURIComponent
const speciesEntryPath = (ref: string) => `/species-entries/${enc(ref)}`
const calculationPath = (ref: string) => `/calculations/${enc(ref)}`
const geometryPath = (ref: string) => `/geometries/${enc(ref)}`
const optionalText = (value: string | null | undefined) => (value ? value : undefined)

/** A group of records stood for by its count, opening the section of this page that lists them. */
function countNode(id: string, kind: RecordKind, labels: [one: string, many: string], relation: string, total: number, to: string | null): NeighbourNode {
    return { id, kind, label: total === 1 ? labels[0] : labels[1], relation, to, found: `${total} found` }
}

const NBSP = "\u00a0"
const together = (phrase: string) => phrase.replaceAll(" ", NBSP)

/**
 * The molecule-version nodes for both sides of a reaction: `perSide` of each, then one "N more" row for the rest. A
 * participant whose label another one shares (the same formula on both sides) carries its SMILES as its second line.
 */
function participantNodes(
    reactants: readonly ParticipantInput[],
    products: readonly ParticipantInput[],
    perSide: number,
    relationSuffix: string,
    moreTo: string | null,
    moreRelation: string,
): NeighbourNode[] {
    const unique = (list: readonly ParticipantInput[]) => {
        const seen = new Set<string>()
        return list.filter((p) => {
            if (seen.has(p.entryRef)) return false
            seen.add(p.entryRef)
            return true
        })
    }
    const labelOf = (p: ParticipantInput) => p.formula || p.smiles || p.entryRef
    const sides = [
        { role: "Reactant", list: unique(reactants) },
        { role: "Product", list: unique(products) },
    ]
    const labelCounts = new Map<string, number>()
    for (const { list } of sides) for (const p of list) labelCounts.set(labelOf(p), (labelCounts.get(labelOf(p)) ?? 0) + 1)
    const nodes: NeighbourNode[] = []
    let hidden = 0
    for (const { role, list } of sides) {
        list.slice(0, perSide).forEach((p, index) => {
            const shared = (labelCounts.get(labelOf(p)) ?? 0) > 1 && !!p.smiles && p.smiles !== labelOf(p)
            nodes.push({
                id: `${role.toLowerCase()}-${index}`,
                kind: "species-entry",
                label: labelOf(p),
                relation: `${role}${relationSuffix}`,
                ...(shared ? { sub: `${role}, ${p.smiles}` } : {}),
                to: speciesEntryPath(p.entryRef),
                found: null,
            })
        })
        hidden += Math.max(0, list.length - perSide)
    }
    if (hidden > 0) {
        nodes.push({
            id: "participants-more", kind: "species-entry", label: `${hidden} more molecule ${hidden === 1 ? "version" : "versions"}`,
            relation: moreRelation, to: moreTo, found: null,
        })
    }
    return nodes
}

type CalcLink = { calculation_ref: string; calculation_type: string }

/** Calculation page: its owner, conformer, geometries, related calculations and level of theory. */
export function calculationNodes(input: {
    owner:
        | { kind: "species_entry"; entryRef: string; formula: string | null }
        | { kind: "transition_state_entry"; entryRef: string; reactionEntryRef: string | null }
        | null
    conformer: { groupRef: string; groupName: string | null; observationRef: string } | null
    /** `null` when the page did not load the list. */
    inputGeometryRefs: readonly string[] | null
    outputGeometryRefs: readonly string[] | null
    /** `null` when the page did not load the dependency list. */
    dependencies: readonly { direction: string; parent_calculation_ref: string; child_calculation_ref: string }[] | null
    levelOfTheory: { ref: string | null; label: string } | null
    /** True when the single input geometry was read out of a deposited input file rather than deposited itself. */
    inputExtractedFromArtifact?: boolean
}): NeighbourNode[] {
    const nodes: NeighbourNode[] = []
    const { owner, conformer } = input
    if (owner?.kind === "species_entry") {
        nodes.push({
            id: "owner", kind: "species-entry", label: "Molecule version", relation: "Run on this molecule version",
            sub: optionalText(owner.formula), to: speciesEntryPath(owner.entryRef), found: null,
        })
    } else if (owner?.kind === "transition_state_entry") {
        nodes.push({
            id: "owner", kind: "transition-state", label: "Transition state", relation: "Run on this saddle point",
            to: `/transition-state-entries/${enc(owner.entryRef)}`, found: null,
        })
        if (owner.reactionEntryRef) {
            nodes.push({
                id: "reaction-entry", kind: "reaction", label: "Reaction version", relation: "The reaction this saddle point belongs to",
                to: `/reaction-entries/${enc(owner.reactionEntryRef)}`, found: null,
            })
        }
    }
    if (conformer) {
        nodes.push({
            id: "conformer-group", kind: "conformer-group", label: "Conformer group", relation: "The shape this calculation observed",
            ...(conformer.groupName ? { sub: conformer.groupName } : { ref: conformer.groupRef }),
            to: `/conformer-groups/${enc(conformer.groupRef)}`, found: null,
        })
        nodes.push({
            id: "conformer-observation", kind: "conformer-observation", label: "Conformer observation", relation: "The observation it belongs to",
            ref: conformer.observationRef, to: `/conformer-observations/${enc(conformer.observationRef)}`, found: null,
        })
    }
    const inputs = input.inputGeometryRefs ?? []
    const outputs = input.outputGeometryRefs ?? []
    const distinct = [...new Set([...inputs, ...outputs])]
    if (distinct.length === 1) {
        // One geometry in both lists reads as the output unless the starting geometry was extracted from the input file.
        const sameInBoth = inputs.length === 1 && outputs.length === 1
        const roles = sameInBoth
            ? (input.inputExtractedFromArtifact ? "input and output" : "output")
            : [inputs.length > 0 ? "input" : null, outputs.length > 0 ? "output" : null].filter(Boolean).join(" and ")
        nodes.push({
            id: "geometry", kind: "geometry", label: "Geometry", relation: "Geometry of this calculation", sub: roles,
            to: geometryPath(distinct[0]), found: null,
        })
    } else if (distinct.length > 1) {
        nodes.push({
            id: "geometry", kind: "geometry", label: "Geometries", relation: "Geometries of this calculation",
            sub: `${inputs.length} input, ${outputs.length} output`, to: "#geometries-heading", found: null,
        })
    }
    if (input.dependencies) {
        const before = new Set(input.dependencies.filter((d) => d.direction === "child").map((d) => d.parent_calculation_ref))
        const after = new Set(input.dependencies.filter((d) => d.direction === "parent").map((d) => d.child_calculation_ref))
        const parts = [before.size > 0 ? together(`depends on ${before.size}`) : null, after.size > 0 ? together(`used by ${after.size}`) : null].filter(Boolean)
        if (parts.length > 0) {
            nodes.push({
                id: "related", kind: "calculation", label: "Related calculations", relation: "Listed under Related calculations",
                sub: parts.join(", "), to: "#dependencies-heading", found: null,
            })
        }
    }
    if (input.levelOfTheory) {
        nodes.push({
            id: "level-of-theory", kind: "level-of-theory", label: "Level of theory", relation: "The method it ran at",
            sub: input.levelOfTheory.label,
            to: input.levelOfTheory.ref ? `/methods/${enc(input.levelOfTheory.ref)}` : null, found: null,
        })
    }
    return nodes
}

/** Conformer group page: its molecule version, what it holds, and the records that name it. */
export function conformerGroupNodes(input: {
    entry: { ref: string; formula: string | null }
    observationCount: number
    /** The only observation's ref, when there is exactly one. */
    onlyObservationRef: string | null
    calculationCount: number
    geometryCount: number
    /** `null` until the usage read has come back. */
    thermoCount: number | null
    kineticsCount: number | null
}): NeighbourNode[] {
    const nodes: NeighbourNode[] = [{
        id: "entry", kind: "species-entry", label: "Molecule version", relation: "The molecule version this shape belongs to",
        sub: optionalText(input.entry.formula), to: speciesEntryPath(input.entry.ref), found: null,
    }]
    if (input.observationCount > 0) {
        nodes.push(countNode(
            "observations", "conformer-observation", ["Conformer observation", "Conformer observations"], "Deposited sightings of this shape", input.observationCount,
            input.observationCount === 1 && input.onlyObservationRef ? `/conformer-observations/${enc(input.onlyObservationRef)}` : "#observation-ledger",
        ))
    }
    if (input.calculationCount > 0) {
        nodes.push(countNode("calculations", "calculation", ["Calculation", "Calculations"], "Behind its observations", input.calculationCount, "#observation-ledger"))
    }
    if (input.geometryCount > 0) {
        nodes.push(countNode("geometries", "geometry", ["Geometry", "Geometries"], "Stored for this shape", input.geometryCount, "#geometry-ledger"))
    }
    if (input.thermoCount) {
        nodes.push(countNode("thermo", "thermo", ["Thermochemistry", "Thermochemistry"], "Records that use this shape", input.thermoCount, "#used-by-heading"))
    }
    if (input.kineticsCount) {
        nodes.push(countNode("kinetics", "kinetics", ["Rate record", "Rate records"], "Records that use this shape", input.kineticsCount, "#used-by-heading"))
    }
    return nodes
}

/** Conformer observation page: its group, molecule version, calculations and geometries. */
export function conformerObservationNodes(input: {
    /** `null` when the read hides the group. */
    group: { ref: string; name: string | null } | null
    entry: { ref: string; formula: string | null }
    calculationCount: number
    geometryCount: number
}): NeighbourNode[] {
    const nodes: NeighbourNode[] = []
    if (input.group) {
        nodes.push({
            id: "group", kind: "conformer-group", label: "Conformer group", relation: "The shape this observation sights",
            ...(input.group.name ? { sub: input.group.name } : { ref: input.group.ref }),
            to: `/conformer-groups/${enc(input.group.ref)}`, found: null,
        })
    }
    nodes.push({
        id: "entry", kind: "species-entry", label: "Molecule version", relation: "The molecule version it belongs to",
        sub: optionalText(input.entry.formula), to: speciesEntryPath(input.entry.ref), found: null,
    })
    if (input.calculationCount > 0) {
        nodes.push(countNode("calculations", "calculation", ["Calculation", "Calculations"], "Behind this observation", input.calculationCount, "#calc-ledger"))
    }
    if (input.geometryCount > 0) {
        nodes.push(countNode("geometries", "geometry", ["Geometry", "Geometries"], "Stored for this observation", input.geometryCount, "#geometry-ledger"))
    }
    return nodes
}

/** Geometry page: its owner, the calculations that produced it and the ones that took it as input. */
export function geometryNodes(input: {
    owner:
        | { kind: "species_entry"; entryRef: string; formula: string | null }
        | { kind: "transition_state_entry"; entryRef: string }
        | null
    /** `null` when the page did not load the list. */
    producedBy: readonly CalcLink[] | null
    usedAsInputBy: readonly CalcLink[] | null
    typeLabel: (type: string) => string
}): NeighbourNode[] {
    const nodes: NeighbourNode[] = []
    if (input.owner?.kind === "species_entry") {
        nodes.push({
            id: "owner", kind: "species-entry", label: "Molecule version", relation: "The molecule version this geometry is of",
            sub: optionalText(input.owner.formula), to: speciesEntryPath(input.owner.entryRef), found: null,
        })
    } else if (input.owner?.kind === "transition_state_entry") {
        nodes.push({
            id: "owner", kind: "transition-state", label: "Transition state", relation: "The saddle point this geometry is of",
            to: `/transition-state-entries/${enc(input.owner.entryRef)}`, found: null,
        })
    }
    const calculations = (id: string, labels: [string, string], relation: string, section: string, list: readonly CalcLink[]) => {
        if (list.length === 1) {
            nodes.push({
                id, kind: "calculation", label: labels[0], relation, sub: input.typeLabel(list[0].calculation_type),
                to: calculationPath(list[0].calculation_ref), found: null,
            })
        } else if (list.length > 1) {
            nodes.push(countNode(id, "calculation", [labels[1], labels[1]], relation, list.length, section))
        }
    }
    if (input.producedBy) calculations("produced-by", ["Producing calculation", "Producing calculations"], "Produced this geometry", "#provenance-produced-by", input.producedBy)
    if (input.usedAsInputBy) calculations("used-by", ["Calculation using it", "Calculations using it"], "Took it as input", "#provenance-used-as-input-by", input.usedAsInputBy)
    return nodes
}

const TS_PARTICIPANTS_PER_SIDE = 1

/** Transition-state entry page: its reaction entry, the molecule versions on either side and its calculations. */
export function transitionStateEntryNodes(input: {
    reactionEntryRef: string | null
    reactants: ParticipantInput[]
    products: ParticipantInput[]
    calculationCount: number
}): NeighbourNode[] {
    const nodes: NeighbourNode[] = []
    const entryPath = input.reactionEntryRef ? `/reaction-entries/${enc(input.reactionEntryRef)}` : null
    if (entryPath) {
        nodes.push({
            id: "reaction-entry", kind: "reaction", label: "Reaction version", relation: "The deposited reaction it belongs to",
            to: entryPath, found: null,
        })
    }
    nodes.push(...participantNodes(
        input.reactants, input.products, TS_PARTICIPANTS_PER_SIDE, "", entryPath, "Listed on the reaction version",
    ))
    if (input.calculationCount > 0) {
        nodes.push(countNode("calculations", "calculation", ["Calculation", "Calculations"], "Run on this saddle point", input.calculationCount, "#calc-ledger"))
    }
    return nodes
}

const OVERVIEW_ENTRIES = 2
const OVERVIEW_PARTICIPANTS_PER_SIDE = 1

/** Reaction page: some of its reaction entries ("N more" when there are others), then the molecule versions of the first. */
export function reactionOverviewNodes(input: {
    entryRefs: readonly string[]
    total: number
    reactants: ParticipantInput[]
    products: ParticipantInput[]
}): NeighbourNode[] {
    const shown = input.entryRefs.slice(0, OVERVIEW_ENTRIES)
    const nodes: NeighbourNode[] = shown.map((ref, index) => ({
        id: ref, kind: "reaction", label: input.total > 1 ? `Reaction version ${index + 1}` : "Reaction version", relation: "A deposited version of this reaction",
        ref, to: `/reaction-entries/${enc(ref)}`, found: null,
    }))
    const more = input.total - shown.length
    if (more > 0) {
        nodes.push({
            id: "more-entries", kind: "reaction", label: `${more} more under Reaction entries`,
            relation: "Listed under Reaction entries", to: "#entries-heading", found: null,
        })
    }
    const firstEntry = input.entryRefs[0] ? `/reaction-entries/${enc(input.entryRefs[0])}` : null
    nodes.push(...participantNodes(
        input.reactants, input.products, OVERVIEW_PARTICIPANTS_PER_SIDE, ", first reaction version", firstEntry,
        "Listed on the first reaction version",
    ))
    return nodes
}

/** Level-of-theory page: the calculations run at it, the composite recipe it is bound to, and its corrections. */
export function levelOfTheoryNodes(input: {
    calculationCount: number
    compositeScheme: { ref: string; label: string } | null
    schemeCount: number | null
    scaleFactorCount: number | null
}): NeighbourNode[] {
    const nodes: NeighbourNode[] = []
    if (input.calculationCount > 0) {
        nodes.push(countNode("calculations", "calculation", ["Calculation", "Calculations"], "Run at this level of theory", input.calculationCount, "#lot-usage-heading"))
    }
    if (input.compositeScheme) {
        nodes.push({
            id: "composite", kind: "level-of-theory", label: "Composite recipe", relation: "The recipe this level is bound to",
            sub: input.compositeScheme.label, to: `/methods/composite-schemes/${enc(input.compositeScheme.ref)}`, found: null,
        })
    }
    if (input.schemeCount) {
        nodes.push(countNode("schemes", "level-of-theory", ["Correction scheme", "Correction schemes"], "Deposited for this level", input.schemeCount, "#lot-schemes-heading"))
    }
    if (input.scaleFactorCount) {
        nodes.push(countNode("scale-factors", "level-of-theory", ["Frequency scale factor", "Frequency scale factors"], "Deposited for this level", input.scaleFactorCount, "#lot-fsf-heading"))
    }
    return nodes
}

/** Network page: the molecule versions and reaction versions it names, and its solves, from the record the page loaded. */
export function networkNodes(input: {
    speciesEntryRefs: readonly string[]
    reactionEntryRefs: readonly string[]
    solveCount: number
}): NeighbourNode[] {
    const nodes: NeighbourNode[] = []
    const species = input.speciesEntryRefs
    if (species.length === 1) {
        nodes.push({
            id: "species", kind: "species-entry", label: "Molecule version", relation: "Takes part in this network",
            ref: species[0], to: speciesEntryPath(species[0]), found: null,
        })
    } else if (species.length > 1) {
        nodes.push(countNode("species", "species-entry", ["Molecule version", "Molecule versions"], "Take part in this network", species.length, "#evidence-heading"))
    }
    const reactions = input.reactionEntryRefs
    if (reactions.length === 1) {
        nodes.push({
            id: "reactions", kind: "reaction", label: "Reaction version", relation: "A step of this network",
            ref: reactions[0], to: `/reaction-entries/${enc(reactions[0])}`, found: null,
        })
    } else if (reactions.length > 1) {
        nodes.push(countNode("reactions", "reaction", ["Reaction version", "Reaction versions"], "Steps of this network", reactions.length, "#reactions-heading"))
    }
    if (input.solveCount > 0) {
        nodes.push(countNode("solves", "network", ["Network solve", "Network solves"], "Deposited solves of this network", input.solveCount, "#evidence-heading"))
    }
    return nodes
}
