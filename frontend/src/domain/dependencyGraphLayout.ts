/**
 * Pure geometry for `CalculationDependencyGraph.tsx` -- no charting library,
 * same precedent as `ThermoCpChart.tsx`/`domain/thermoCpChartLayout.ts`
 * (hand-rolled SVG, this project has no plotting dependency in
 * `package.json`). Kept out of the component so the math is unit-testable
 * without a DOM, and so the component itself stays a thin render of
 * whatever this module computes -- jsdom lays out and measures nothing
 * (no `getBBox`), so box sizes below are estimated from character counts
 * at a fixed monospace step, never measured at render time. That estimate
 * is generous enough for this archive's `calc_<24 lowercase-alnum>` refs
 * (~29 characters) to fit on one line; a real browser is where the visual
 * fit is actually confirmed (screenshots at 1920/1100/680 in the PR).
 *
 * Two full, independent layouts -- `computeWideLayout` (parents/children
 * spread horizontally either side of the centre) and `computeNarrowLayout`
 * (everything in one vertical column, edges routed through a side lane
 * gutter). `CalculationDependencyGraph.tsx` picks between them at RENDER
 * TIME, from a real measured container width (`ResizeObserver`) compared
 * against `computeWideLayout(...).width` -- never a fixed breakpoint and
 * never two SVGs left in the DOM for CSS to choose between. A fixed
 * `680px` media query (an earlier version of this module) let the wide
 * layout scale itself down via `width: 100%` on any container narrower
 * than its own natural width but wider than 680px -- MEASURED: at a
 * 1100px container with 3 children the SVG scaled to 0.913x, and its
 * smallest text (`--type-label-font`, 11.52px) rendered at 10.52px, below
 * this app's own accessible floor. Comparing the real container width to
 * the wide layout's own computed width switches to the narrow layout
 * BEFORE any such shrink happens, so the wide layout only ever renders at
 * exactly 1.000 scale (`CalculationDependencyGraph.tsx`'s inline
 * `maxWidth` still caps it from scaling UP past that).
 *
 * ---------------------------------------------------------------------
 * Band ordering (wide layout) -- why a sibling's OWN distance from the
 * centre column decides how deep its band is, not array order:
 *
 * Every edge in a tier shares the SAME vertical "trunk" segment near the
 * centre (`centreX`) for the run from ITS OWN band down to the centre's
 * own edge -- which means edge i's trunk passes through every band
 * DEEPER than its own (every `j >= i`), never a shallower one. Band 1
 * (nearest the sibling row) is therefore the only band nothing ever
 * shares a trunk through; every deeper band is crossed by every
 * shallower edge's trunk. A label is placed at the SIBLING end of its
 * own run (`labelX = box.x`, not the midpoint), which keeps it clear of
 * `centreX` for any sibling whose box is far enough from centre -- but a
 * sibling positioned AT (or very near) `centreX` -- the middle box of an
 * odd-count row, or any row whose per-tier label is wide relative to how
 * far that sibling sits from centre -- still has a label that reaches
 * `centreX` regardless of which band it draws in. `assignBandOrder`
 * computes each edge's own CLEARANCE (`|box.x - centreX| -
 * ownLabelWidth / 2`, how far its label falls short of reaching
 * `centreX`) and gives the edge with the WORST (most negative) clearance
 * band 1 -- the one band no other edge's trunk ever crosses. Every
 * deeper band IS crossed, by every shallower edge, but only a sibling
 * with bad clearance has a label anywhere near `centreX` to be hit --
 * assigning the worst clearance to the one uncrossed position is what
 * keeps every crossing outside every label.
 * ---------------------------------------------------------------------
 */

import type { CalculationDependency } from "../api/calculationApi"
import { typeLabel } from "./calculationTypeFormat"
import { dependencyEdgeLabel } from "./dependencyWording"

// ---------------------------------------------------------------------------
// Node / edge extraction (direction-neutral, orientation-agnostic)
// ---------------------------------------------------------------------------

export interface DependencyGraphEdgeSpec {
    role: string
    label: string
    /** The OTHER calculation's ref -- never `ownRef`. */
    otherRef: string
    /** Which side of the centre this edge's other-calculation sits on. */
    tier: "parent" | "child"
}

export interface DependencyGraphModel {
    ownRef: string
    ownType: string
    /** Distinct parent refs, in first-seen order. */
    parentRefs: string[]
    /** Distinct child refs, in first-seen order. */
    childRefs: string[]
    /** One entry per dependency row (NOT deduped -- two roles pointing at
     * the same ref are two edges into the same node). */
    edges: DependencyGraphEdgeSpec[]
}

/**
 * `dep.direction` names which side of the edge THIS calculation (`ownRef`)
 * sits on, not the edge's own parent->child direction -- see
 * `dependencySentence`'s docstring in `CalculationDetailPage.tsx`, which
 * this mirrors:
 * - `direction === "child"`: this calc is the CHILD; `parent_calculation_ref`
 *   names a PARENT (tier "parent", drawn above centre).
 * - `direction === "parent"`: this calc is the PARENT; `child_calculation_ref`
 *   names a CHILD (tier "child", drawn below centre).
 */
export function buildDependencyGraphModel(
    ownRef: string,
    ownType: string,
    dependencies: CalculationDependency[],
): DependencyGraphModel {
    const parentRefs: string[] = []
    const childRefs: string[] = []
    const edges: DependencyGraphEdgeSpec[] = []
    const seenParents = new Set<string>()
    const seenChildren = new Set<string>()

    for (const dep of dependencies) {
        const label = dependencyEdgeLabel(dep.role)
        if (dep.direction === "child") {
            const otherRef = dep.parent_calculation_ref
            if (!seenParents.has(otherRef)) {
                seenParents.add(otherRef)
                parentRefs.push(otherRef)
            }
            edges.push({ role: dep.role, label, otherRef, tier: "parent" })
        } else {
            const otherRef = dep.child_calculation_ref
            if (!seenChildren.has(otherRef)) {
                seenChildren.add(otherRef)
                childRefs.push(otherRef)
            }
            edges.push({ role: dep.role, label, otherRef, tier: "child" })
        }
    }

    return { ownRef, ownType, parentRefs, childRefs, edges }
}

/** "2 parents, 1 child" / "1 parent" / "3 children" -- the `aria-label`
 * summarising the whole graph. Never mentions a zero-count side. */
export function dependencyGraphAriaLabel(parentCount: number, childCount: number): string {
    const parts: string[] = []
    if (parentCount > 0) parts.push(`${parentCount} ${parentCount === 1 ? "parent" : "parents"}`)
    if (childCount > 0) parts.push(`${childCount} ${childCount === 1 ? "child" : "children"}`)
    return parts.join(", ")
}

// ---------------------------------------------------------------------------
// Shared box-sizing estimate
// ---------------------------------------------------------------------------

// Node ref text: `--type-data-font` (.8125rem/13px IBM Plex Mono).
const CHAR_W = 7.5
const NODE_MIN_W = 140
const NODE_PAD_X = 24
const NODE_H = 40

// Edge labels AND the centre node's type pill: `--type-label-font` /
// `--type-label-strong-font` (both .72rem/11.52px IBM Plex Mono,
// uppercase, tracked). MEASURED (post-review): this app's real font at
// this exact size/weight/tracking/transform combination -- not the
// node-ref estimate above, which is a different size -- averages 7.6px
// per character, not the 6.6 this module used before; "IRC started from
// this geometry" (30 characters incl. spaces, the longest label this
// table produces) sets at 228px, not 198px. `SMALL_PAD` is 6px on each
// side (12px total), the minimum slack a text-vs-background-box test
// checks for on that exact longest label.
const SMALL_CHAR_W = 7.6
const SMALL_PAD = 6
const LABEL_H = 18
const PILL_H = 18

// Centre node: padding, the pill, a gap, then the ref line.
const CENTRE_PAD_TOP = 10
const CENTRE_PILL_GAP = 8
const CENTRE_REF_LINE_H = 24
const CENTRE_PAD_BOTTOM = 12
const CENTRE_H = CENTRE_PAD_TOP + PILL_H + CENTRE_PILL_GAP + CENTRE_REF_LINE_H + CENTRE_PAD_BOTTOM

function nodeWidth(ref: string): number {
    return Math.max(NODE_MIN_W, Math.round(ref.length * CHAR_W) + NODE_PAD_X)
}

/** Shared sizing for an edge label OR the centre node's type pill --
 * both draw at the same small, tracked, uppercase mono step. */
function smallTextBoxWidth(text: string): number {
    return Math.round(text.length * SMALL_CHAR_W) + SMALL_PAD * 2
}

export interface LayoutNode {
    ref: string
    tier: "parent" | "centre" | "child"
    type: string | null
    x: number
    y: number
    width: number
    height: number
    /** Centre node only: the type-pill box drawn INSIDE the node (a real
     * `<rect rx>`, not bare text -- see `.value-pill`'s own treatment,
     * which this matches: `--accent-50` fill, `--accent-300` stroke).
     * `label` is `typeLabel(type)` -- computed once here so the component
     * never re-derives what text the pill sizing was based on. */
    pill?: { width: number; height: number; label: string }
}

export interface LayoutEdge {
    role: string
    label: string
    fromRef: string
    toRef: string
    /** SVG `<path d>`, always drawn FROM the source (parent) TO the
     * destination (child) -- `marker-end` (always the last point) is
     * therefore always the arrowhead in the parent -> child direction.
     * Every segment is axis-aligned (a plain elbow, never a diagonal). */
    path: string
    labelX: number
    labelY: number
    labelWidth: number
    labelHeight: number
    /** Narrow layout only: the lane `x` this edge's stub and label both
     * anchor to (`labelX - labelWidth / 2 === laneX`, checked directly by
     * `dependencyGraphLayout.test.ts`). Undefined for a wide-layout edge,
     * which has no lane. */
    laneX?: number
}

export interface GraphLayout {
    width: number
    height: number
    nodes: LayoutNode[]
    edges: LayoutEdge[]
}

function centrePillFor(ownType: string): { width: number; height: number; label: string } {
    const label = typeLabel(ownType)
    return { width: smallTextBoxWidth(label), height: PILL_H, label }
}

// ---------------------------------------------------------------------------
// Wide layout: parents in a horizontal row above, children in a horizontal
// row below, each edge a 3-segment elbow into the centre. See the module
// docstring's "Band ordering" section for why band DEPTH is assigned by
// each edge's own clearance from `centreX`, not by array order.
// ---------------------------------------------------------------------------

const WIDE_GAP_X_MIN = 24
const WIDE_MARGIN = 24
const WIDE_BAND_STEP = 26

function layoutWideRow(
    refs: string[], rowCenterX: number, gapX: number,
): { boxes: Map<string, { x: number; width: number }>; rowWidth: number } {
    const widths = refs.map(nodeWidth)
    const rowWidth = widths.reduce((sum, w) => sum + w, 0) + gapX * Math.max(0, refs.length - 1)
    let cursor = rowCenterX - rowWidth / 2
    const boxes = new Map<string, { x: number; width: number }>()
    refs.forEach((ref, index) => {
        const width = widths[index]
        boxes.set(ref, { x: cursor + width / 2, width })
        cursor += width + gapX
    })
    return { boxes, rowWidth }
}

/**
 * See the module docstring's "Band ordering" section. Returns each edge's
 * 1-indexed band depth.
 *
 * A PARENT-tier path's shared run (at `centreX`) is its LAST segment,
 * from its own band down to `centreTopY` -- so edge i's shared run spans
 * every band DEEPER than its own (`j >= i`), and band 1 (nearest the
 * sibling row) is the only band nothing ever shares that column through.
 *
 * A CHILD-tier path's shared run is its FIRST segment instead, from
 * `centreBottomY` down to its own band -- the mirror image -- so edge i's
 * shared run spans every band SHALLOWER than its own (`j <= i`), and
 * band N (nearest the sibling row, THE LAST band) is the one nothing
 * else's shared run reaches.
 *
 * Either way, exactly one band position is never shared through by any
 * OTHER edge's trunk; `uncrossedBand` tells this function which end that
 * is for the tier being laid out, and the edge with the WORST (most
 * negative) clearance -- the one most likely to have a label reaching
 * `centreX` -- is the one assigned to it. */
function assignBandOrder(
    tierEdges: DependencyGraphEdgeSpec[],
    boxes: Map<string, { x: number }>,
    centreX: number,
    uncrossedBand: "shallowest" | "deepest",
): Map<DependencyGraphEdgeSpec, number> {
    const withClearance = tierEdges.map((edge, arrayIndex) => {
        const boxX = boxes.get(edge.otherRef)!.x
        const clearance = Math.abs(boxX - centreX) - smallTextBoxWidth(edge.label) / 2
        return { edge, arrayIndex, clearance }
    })
    // Worst (most negative) clearance goes to whichever end is the
    // uncrossed band for this tier; best (most positive) clearance goes
    // to the other end, since it is crossed regardless but safely --
    // its own label falls well short of centreX. Stable on the original
    // array order for a genuine tie.
    const direction = uncrossedBand === "shallowest" ? 1 : -1
    withClearance.sort((a, b) => direction * (a.clearance - b.clearance) || a.arrayIndex - b.arrayIndex)
    const order = new Map<DependencyGraphEdgeSpec, number>()
    withClearance.forEach((item, index) => order.set(item.edge, index + 1))
    return order
}

export function computeWideLayout(model: DependencyGraphModel): GraphLayout {
    const { ownRef, ownType, parentRefs, childRefs, edges } = model
    const centreWidth = nodeWidth(ownRef)
    const pill = centrePillFor(ownType)

    const parentEdges = edges.filter((e) => e.tier === "parent")
    const childEdges = edges.filter((e) => e.tier === "child")
    // The gap between sibling boxes must be wide enough that a label
    // CENTRED ON ITS OWN BOX (see below) cannot reach a neighbour's box
    // OR a neighbour's label -- sized off the widest label in the tier,
    // not a flat constant, since a long label ("IRC started from this
    // geometry", 228px) is wider than this app's own minimum node box
    // (140px).
    const maxParentLabelW = Math.max(0, ...parentEdges.map((e) => smallTextBoxWidth(e.label)))
    const maxChildLabelW = Math.max(0, ...childEdges.map((e) => smallTextBoxWidth(e.label)))
    const parentGapX = Math.max(WIDE_GAP_X_MIN, maxParentLabelW / 2 + 16)
    const childGapX = Math.max(WIDE_GAP_X_MIN, maxChildLabelW / 2 + 16)

    const parentWidths = parentRefs.map(nodeWidth)
    const childWidths = childRefs.map(nodeWidth)
    const parentRowWidth = parentWidths.reduce((s, w) => s + w, 0) + parentGapX * Math.max(0, parentRefs.length - 1)
    const childRowWidth = childWidths.reduce((s, w) => s + w, 0) + childGapX * Math.max(0, childRefs.length - 1)
    const contentWidth = Math.max(centreWidth, parentRowWidth, childRowWidth)
    const svgWidth = contentWidth + WIDE_MARGIN * 2
    const centreX = svgWidth / 2

    const parentEdgeCount = parentEdges.length
    const childEdgeCount = childEdges.length
    const parentTierGap = parentRefs.length > 0 ? WIDE_BAND_STEP * (parentEdgeCount + 1) : 0
    const childTierGap = childRefs.length > 0 ? WIDE_BAND_STEP * (childEdgeCount + 1) : 0

    let cursorY = WIDE_MARGIN
    const nodes: LayoutNode[] = []
    let parentRow: { boxes: Map<string, { x: number; width: number }> } | null = null
    let parentTierBottomY = cursorY

    if (parentRefs.length > 0) {
        const row = layoutWideRow(parentRefs, centreX, parentGapX)
        parentRow = row
        parentRefs.forEach((ref) => {
            const box = row.boxes.get(ref)!
            nodes.push({ ref, tier: "parent", type: null, x: box.x, y: cursorY + NODE_H / 2, width: box.width, height: NODE_H })
        })
        parentTierBottomY = cursorY + NODE_H
        cursorY = parentTierBottomY + parentTierGap
    }

    const centreTopY = cursorY
    nodes.push({
        ref: ownRef, tier: "centre", type: ownType, x: centreX, y: centreTopY + CENTRE_H / 2,
        width: centreWidth, height: CENTRE_H, pill,
    })
    const centreBottomY = centreTopY + CENTRE_H
    cursorY = centreBottomY + childTierGap

    let childRow: { boxes: Map<string, { x: number; width: number }> } | null = null
    const childTierTopY = cursorY
    if (childRefs.length > 0) {
        const row = layoutWideRow(childRefs, centreX, childGapX)
        childRow = row
        childRefs.forEach((ref) => {
            const box = row.boxes.get(ref)!
            nodes.push({ ref, tier: "child", type: null, x: box.x, y: cursorY + NODE_H / 2, width: box.width, height: NODE_H })
        })
        cursorY = cursorY + NODE_H
    }

    const svgHeight = cursorY + WIDE_MARGIN

    const parentBandOrder = parentRow ? assignBandOrder(parentEdges, parentRow.boxes, centreX, "shallowest") : new Map()
    const childBandOrder = childRow ? assignBandOrder(childEdges, childRow.boxes, centreX, "deepest") : new Map()

    const edgesOut: LayoutEdge[] = []
    for (const edge of edges) {
        if (edge.tier === "parent" && parentRow) {
            const box = parentRow.boxes.get(edge.otherRef)!
            const bandIndex = parentBandOrder.get(edge)!
            const bandY = parentTierBottomY + WIDE_BAND_STEP * bandIndex
            const path = `M ${box.x} ${parentTierBottomY} L ${box.x} ${bandY} L ${centreX} ${bandY} L ${centreX} ${centreTopY}`
            const width = smallTextBoxWidth(edge.label)
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: edge.otherRef, toRef: ownRef, path,
                // Label anchored at the SIBLING end of the band (its own
                // box's x), not the midpoint toward centreX -- see the
                // module docstring's "Band ordering" section for why.
                labelX: box.x, labelY: bandY, labelWidth: width, labelHeight: LABEL_H,
            })
        } else if (edge.tier === "child" && childRow) {
            const box = childRow.boxes.get(edge.otherRef)!
            const bandIndex = childBandOrder.get(edge)!
            const bandY = centreBottomY + WIDE_BAND_STEP * bandIndex
            const path = `M ${centreX} ${centreBottomY} L ${centreX} ${bandY} L ${box.x} ${bandY} L ${box.x} ${childTierTopY}`
            const width = smallTextBoxWidth(edge.label)
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: ownRef, toRef: edge.otherRef, path,
                labelX: box.x, labelY: bandY, labelWidth: width, labelHeight: LABEL_H,
            })
        }
    }

    return { width: svgWidth, height: svgHeight, nodes, edges: edgesOut }
}

// ---------------------------------------------------------------------------
// Narrow layout: single vertical column (parents, then centre, then
// children), each non-centre node's edge routed through its OWN lane in a
// side gutter -- both the lane's `x` (unique per edge, across BOTH tiers)
// and the entry/exit point on the centre's own edge (staggered per lane
// index, parents above `centreCenterY`, children below it) are unique per
// edge, so N siblings stacked in the same tier -- or a parent and a child
// sharing a lane index -- never share so much as one (x, y) point.
// ---------------------------------------------------------------------------

const NARROW_MARGIN = 20
const NARROW_GAP_Y = 14
const NARROW_TIER_GAP_MIN = 22
const NARROW_LANE_GUTTER = 20
// How far each successive lane's entry/exit point sits from
// `centreCenterY` -- parents at `centreCenterY - STEP * laneIndex`
// (above), children at `centreCenterY + STEP * laneIndex` (below). MUST
// exceed `LABEL_H` (18): each label is centred ON its own entry/exit
// point (see the edge-building loop below), so two lanes staggered by
// LESS than a label's own height leave their label bands overlapping
// each other even though the two POINTS themselves are distinct --
// MEASURED (post-review): a 6px stagger put lane 2's entry point inside
// lane 1's own 18px-tall label band. `NARROW_TIER_GAP` (the space
// reserved between the nearest sibling row and the centre node) scales
// with this and the lane count so the staggered entries never drift far
// enough to reach an actual sibling node's row.
const NARROW_ENTRY_STAGGER = LABEL_H + 4

export function computeNarrowLayout(model: DependencyGraphModel): GraphLayout {
    const { ownRef, ownType, parentRefs, childRefs, edges } = model
    const pill = centrePillFor(ownType)
    const allWidths = [nodeWidth(ownRef), ...parentRefs.map(nodeWidth), ...childRefs.map(nodeWidth)]
    const colWidth = Math.max(...allWidths)
    const centerX = NARROW_MARGIN + colWidth / 2

    const parentEdges = edges.filter((e) => e.tier === "parent")
    const childEdges = edges.filter((e) => e.tier === "child")
    const laneCount = Math.max(parentEdges.length, childEdges.length, 1)
    const gutterX = centerX + colWidth / 2 + NARROW_LANE_GUTTER
    const maxLabelWidth = Math.max(0, ...edges.map((e) => smallTextBoxWidth(e.label)))
    // A modest fixed step is enough between lanes -- each lane's own
    // LABEL sits at its centre-side end (`entryY`/`exitY`, tightly
    // clustered near `centreCenterY` and staggered by
    // `NARROW_ENTRY_STAGGER`), never at the raw `nodeY` a deeper lane's
    // node-side stub runs through, so lanes no longer need the widest
    // label's own width just to keep two lanes' LABELS apart -- only
    // enough that neighbouring lanes' vertical runs read as visually
    // distinct.
    const laneStep = 24
    // The SVG's own width still has to reach past the LAST lane's own
    // label (left-anchored there, extending further right by its own
    // width) -- `maxLabelWidth`, not just the lane positions themselves.
    const svgWidth = gutterX + laneStep * laneCount + maxLabelWidth + NARROW_MARGIN

    // Reserves enough room between the nearest sibling row and the centre
    // node for every staggered entry/exit point (see `NARROW_ENTRY_
    // STAGGER`'s own comment) to land inside the gap, never inside a
    // sibling row.
    const parentTierGap = Math.max(NARROW_TIER_GAP_MIN, NARROW_ENTRY_STAGGER * parentEdges.length + 10)
    const childTierGap = Math.max(NARROW_TIER_GAP_MIN, NARROW_ENTRY_STAGGER * childEdges.length + 10)

    const nodes: LayoutNode[] = []
    let y = NARROW_MARGIN
    const parentCenters = new Map<string, number>()
    for (const ref of parentRefs) {
        const width = nodeWidth(ref)
        const centerY = y + NODE_H / 2
        nodes.push({ ref, tier: "parent", type: null, x: centerX, y: centerY, width, height: NODE_H })
        parentCenters.set(ref, centerY)
        y += NODE_H + NARROW_GAP_Y
    }
    if (parentRefs.length > 0) y += parentTierGap - NARROW_GAP_Y

    const centreCenterY = y + CENTRE_H / 2
    nodes.push({
        ref: ownRef, tier: "centre", type: ownType, x: centerX, y: centreCenterY,
        width: nodeWidth(ownRef), height: CENTRE_H, pill,
    })
    y += CENTRE_H

    if (childRefs.length > 0) y += childTierGap

    const childCenters = new Map<string, number>()
    for (const ref of childRefs) {
        const width = nodeWidth(ref)
        const centerY = y + NODE_H / 2
        nodes.push({ ref, tier: "child", type: null, x: centerX, y: centerY, width, height: NODE_H })
        childCenters.set(ref, centerY)
        y += NODE_H + NARROW_GAP_Y
    }
    const svgHeight = (childRefs.length > 0 ? y - NARROW_GAP_Y : y) + NARROW_MARGIN

    const rightEdgeX = centerX + colWidth / 2
    // Every label is LEFT-anchored at its own lane (`labelX` is the box's
    // CENTER, so `laneX + halfWidth` puts its left edge exactly at the
    // lane) -- keeps the whole label box at x >= laneX > rightEdgeX,
    // strictly clear of every node in the column regardless of y.
    //
    // `labelY` sits AT the centre-side endpoint (`entryY`/`exitY`), never
    // the midpoint toward the sibling's own `nodeY` -- MEASURED
    // (post-review): a midpoint label can land at almost any y across the
    // whole column, and a DEEPER lane's stub (which crosses every
    // shallower lane's x at that stub's OWN node's y, the same "shared
    // trunk" geometry the wide layout's band ordering exists to avoid)
    // then has a real chance of running straight through it. `entryY`/
    // `exitY` are tightly clustered within `NARROW_ENTRY_STAGGER` of
    // `centreCenterY` (a small, fixed band near the centre node itself),
    // well clear of every stub, which only ever runs at a sibling's own
    // `nodeY` -- far out in the column, never inside that band.
    const edgesOut: LayoutEdge[] = []
    let parentLane = 0
    let childLane = 0
    for (const edge of edges) {
        if (edge.tier === "parent") {
            parentLane += 1
            const laneX = gutterX + laneStep * parentLane
            const nodeY = parentCenters.get(edge.otherRef)!
            // Staggered entry point (never the bare `centreCenterY`) --
            // see the section docstring above: this is what keeps a
            // parent's lane-1 stub and a child's lane-1 stub, which
            // otherwise share both `laneX` and `centreCenterY`, from
            // coinciding.
            const entryY = centreCenterY - NARROW_ENTRY_STAGGER * parentLane
            const path = `M ${rightEdgeX} ${nodeY} L ${laneX} ${nodeY} L ${laneX} ${entryY} L ${rightEdgeX} ${entryY}`
            const width = smallTextBoxWidth(edge.label)
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: edge.otherRef, toRef: ownRef, path,
                labelX: laneX + width / 2, labelY: entryY, labelWidth: width, labelHeight: LABEL_H, laneX,
            })
        } else {
            childLane += 1
            const laneX = gutterX + laneStep * childLane
            const nodeY = childCenters.get(edge.otherRef)!
            const exitY = centreCenterY + NARROW_ENTRY_STAGGER * childLane
            const path = `M ${rightEdgeX} ${exitY} L ${laneX} ${exitY} L ${laneX} ${nodeY} L ${rightEdgeX} ${nodeY}`
            const width = smallTextBoxWidth(edge.label)
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: ownRef, toRef: edge.otherRef, path,
                labelX: laneX + width / 2, labelY: exitY, labelWidth: width, labelHeight: LABEL_H, laneX,
            })
        }
    }

    return { width: svgWidth, height: svgHeight, nodes, edges: edgesOut }
}
